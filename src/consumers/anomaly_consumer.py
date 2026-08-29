"""
Anomaly detection consumer.

Consumes market data ticks from Kafka (same payload as the market data
producer: symbol, price, return_1m, return_5m, volume, fetch_id), runs
them through the tick-level detection pipeline (z-score, isolation
forest, cross-asset correlation), and persists qualifying anomalies to
the anomaly_events table.

This complements the batch anomaly engine (src/detection/anomaly_engine.py),
which scores pre-computed features from market_features on a schedule.
Raw tick storage is owned by the feature consumer, so this consumer only
writes anomaly events.

Usage:
    python -m src.consumers.anomaly_consumer
    KAFKA_BOOTSTRAP_SERVERS=localhost:9092 python -m src.consumers.anomaly_consumer
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from src.api.database import AnomalyEvent, async_session_factory
from src.consumers.base import BaseConsumer
from src.detection.anomaly_engine import SYMBOLS, should_insert_event
from src.detection.explain import build_snapshot_from_pipeline, explain_from_env
from src.detection.lead_lag import augment_description as augment_lead_lag
from src.detection.lead_lag import lead_lag_for
from src.detection.macro_calendar import augment_description, macro_event_for
from src.detection.pipeline import DetectionPipeline

logger = logging.getLogger("anomaly_consumer")

# Warm-up history size per symbol (oldest-first closes) used to preload
# detector state at startup. Covers the largest detector requirement
# (window 100) with margin.
WARMUP_ROWS = 120

# Map tick-level detector types to the flag columns of anomaly_events.
# The EWMA flag is owned by the batch engine; no tick-level detector
# maps to it, so it stays False here.
FLAG_BY_TYPE = {
    "zscore_spike": "z_flag",
    "correlation_break": "pca_flag",
    "isolation_forest_outlier": None,
}


def build_description(symbol: str, anomaly: dict[str, Any]) -> str:
    """Compose a human-readable description from a pipeline anomaly dict."""
    metadata = anomaly.get("metadata", {})
    detector = metadata.get("detector", "unknown")
    parts = [f"Tick-level {anomaly['type']} ({detector} detector)"]

    if "z_score" in metadata:
        parts.append(f"z={metadata['z_score']:.2f}")
    if metadata.get("pair"):
        parts.append(
            f"pair={metadata['pair']} corr={metadata.get('current_correlation')}"
        )
    if "raw_score" in metadata:
        parts.append(f"iforest_score={metadata['raw_score']}")

    context = f"detected on {symbol} at {anomaly['price']:.4f}"
    return f"{', '.join(parts)} {context} — {anomaly['severity']} severity"


class AnomalyConsumer(BaseConsumer):
    """Consumer that detects and persists market anomalies."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pipeline = DetectionPipeline()

    @staticmethod
    async def fetch_warmup_closes() -> dict[str, list[tuple[Any, float]]]:
        """
        Fetch recent closes per symbol (oldest-first) for warm-starting
        the detection pipeline. Returns {} on failure — cold start.
        """
        closes: dict[str, list[tuple[Any, float]]] = {}
        async with async_session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT timestamp, symbol, price FROM (
                        SELECT timestamp, symbol, price,
                               ROW_NUMBER() OVER (PARTITION BY symbol
                                                  ORDER BY timestamp DESC) AS rn
                        FROM market_features
                        WHERE symbol = ANY(:symbols) AND price IS NOT NULL
                    ) sub
                    WHERE rn <= :limit
                    ORDER BY symbol, timestamp ASC
                """),
                {"symbols": list(SYMBOLS), "limit": WARMUP_ROWS},
            )
            for ts, sym, price in result.all():
                closes.setdefault(sym, []).append((ts, float(price)))
        return closes

    @staticmethod
    async def _last_event(session: Any, symbol: str) -> tuple[Optional[datetime], Optional[float]]:
        """Most recent anomaly_events row for a symbol inside the cooldown window."""
        from src.detection.anomaly_engine import ANOMALY_COOLDOWN_MINUTES

        result = await session.execute(
            text("""
                SELECT timestamp, anomaly_score FROM anomaly_events
                WHERE symbol = :symbol
                  AND timestamp >= NOW() - (:minutes * INTERVAL '1 minute')
                ORDER BY timestamp DESC
                LIMIT 1
            """),
            {"symbol": symbol, "minutes": ANOMALY_COOLDOWN_MINUTES},
        )
        row = result.first()
        if row is None:
            return None, None
        return row[0], float(row[1])

    async def process_message(self, key: Optional[str], value: dict[str, Any]) -> None:
        """Process a market tick: run detection, store anomalies."""
        symbol = value.get("symbol", key or "UNKNOWN")
        price = value.get("price")

        if price is None:
            logger.debug("[SKIP] No price in message for %s — skipping", symbol)
            return

        timestamp_str = value.get("timestamp")
        timestamp = (
            datetime.fromisoformat(timestamp_str)
            if timestamp_str
            else datetime.now(timezone.utc)
        )

        anomalies = self.pipeline.detect(
            asset=symbol,
            price=float(price),
            timestamp=timestamp,
        )

        if not anomalies:
            return

        async with async_session_factory() as session:
            try:
                persisted = 0
                for anomaly in anomalies:
                    score = round(float(anomaly["score"]), 3)

                    # Suppress sustained anomalies: skip re-insertion for a
                    # symbol with a recent event unless the score escalated
                    # (same rule as the batch engine).
                    last_ts, last_score = await self._last_event(session, symbol)
                    insert, reason = should_insert_event(
                        score, last_ts, last_score, timestamp
                    )
                    if not insert:
                        logger.info(
                            "[ANOMALY] %s score=%.3f suppressed: %s",
                            symbol,
                            score,
                            reason,
                        )
                        continue

                    flags = {"z_flag": False, "ewma_flag": False, "pca_flag": False}
                    flag_key = FLAG_BY_TYPE.get(anomaly["type"])
                    if flag_key:
                        flags[flag_key] = True

                    # Macro-calendar context: annotate (never suppress)
                    # when the tick coincides with a scheduled release.
                    description = build_description(symbol, anomaly)
                    macro = macro_event_for(timestamp)
                    if macro:
                        description = augment_description(description, timestamp)

                    # Volatility regime recorded at fire time so stored
                    # anomalies are self-explanatory later.
                    regime = anomaly.get("metadata", {}).get("regime")
                    if regime:
                        description += f" [regime: {regime}]"

                    # Lead-lag context: which paired asset likely moved
                    # first (annotation only, same as macro context).
                    lead_lag = lead_lag_for(
                        symbol, self.pipeline.recent_returns()
                    )
                    if lead_lag:
                        description = augment_lead_lag(description, lead_lag)

                    # Optional LLM explanation (high-severity only; null
                    # when off, budget-exhausted, or failed — never blocks
                    # the anomaly from being recorded).
                    explanation = explain_from_env(
                        anomaly,
                        build_snapshot_from_pipeline(
                            self.pipeline, lead_lag=lead_lag, macro_event=macro
                        ),
                    )
                    if explanation:
                        description += f" [explanation: {explanation}]"

                    session.add(AnomalyEvent(
                        timestamp=timestamp,
                        symbol=symbol,
                        anomaly_score=score,
                        z_flag=flags["z_flag"],
                        ewma_flag=flags["ewma_flag"],
                        pca_flag=flags["pca_flag"],
                        description=description,
                        macro_context=macro["name"] if macro else None,
                        llm_explanation=explanation,
                    ))
                    logger.warning(
                        "[ANOMALY] %s score=%.3f type=%s severity=%s",
                        symbol,
                        score,
                        anomaly["type"],
                        anomaly["severity"],
                    )
                    persisted += 1
                await session.commit()
                if persisted:
                    from src.metrics import ANOMALIES_PERSISTED

                    ANOMALIES_PERSISTED.inc(persisted)
            except Exception as e:
                await session.rollback()
                logger.error(
                    "[DB] Failed to persist anomalies for %s: %s",
                    symbol,
                    e,
                    exc_info=True,
                )
                raise


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    consumer = AnomalyConsumer()

    # Warm-start detector state from recent market_features closes so
    # tick-level detection is active from the first tick after a restart.
    try:
        closes = await consumer.fetch_warmup_closes()
        counts = consumer.pipeline.warm_start(prices_by_symbol=closes)
        total = sum(counts.values())
        if total:
            logger.info(
                "[WARM-START] Preloaded %d price observations across %d symbols: %s",
                total,
                len(counts),
                counts,
            )
        else:
            logger.info("[WARM-START] No history available — starting with cold detectors")
    except Exception:
        logger.warning("[WARM-START] Failed — starting with cold detectors", exc_info=True)

    logger.info(
        "Anomaly consumer starting: servers=%s topic=%s group=%s",
        consumer.bootstrap_servers,
        consumer.topic,
        consumer.group_id,
    )
    try:
        await consumer.run()
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")


if __name__ == "__main__":
    asyncio.run(main())
