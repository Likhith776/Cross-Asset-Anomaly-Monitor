"""
Container-less single-process deployment profile ("slim").

Fuses the six runtime services into one process against a plain Postgres:

  fetch loop ──▶ features ──▶ Postgres
       └──────────▶ DetectionPipeline ──▶ alerts ──▶ Postgres
  5-min batch-detect / nightly cleanup (+optional backup) threads
  FastAPI served in-process; Streamlit runs beside it as its own command

Everything analytical is imported from the very modules the Kafka stack
uses — this file is composition glue only, so both profiles share one
implementation of the detection logic. The broker disappears; nothing
else changes. Detector state is warm-started from market_features at
boot exactly like the full stack.

Run:
    python -m src.slim                # API on :8000
    streamlit run dashboard/app.py    # dashboard, separate terminal

Environment:
    DATABASE_URL           required (psycopg2 form)
    SLIM_FETCH_INTERVAL    seconds between fetch cycles   (default 30)
    SLIM_BATCH_INTERVAL    seconds between batch cycles     (default 300)
    SLIM_SYMBOLS           space-separated symbol list      (default all 7)
    API_PORT               uvicorn port                     (default 8000)
    SLIM_BACKUPS           1 enables nightly pg_dump        (default 0)
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("slim")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv() -> None:
    """Minimal .env loader so a bare `python -m src.slim` just works."""
    path = os.path.join(REPO_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

# Container-less default for backup output (Docker profile mounts /backups).
os.environ.setdefault("BACKUP_DIR", os.path.join(REPO_ROOT, "backups"))

FETCH_INTERVAL = int(os.getenv("SLIM_FETCH_INTERVAL", "30"))
BATCH_INTERVAL = int(os.getenv("SLIM_BATCH_INTERVAL", "300"))
API_PORT = int(os.getenv("API_PORT", "8000"))
BACKUPS_ENABLED = os.getenv("SLIM_BACKUPS", "0") == "1"

from src.consumers.anomaly_consumer import FLAG_BY_TYPE, build_description
from src.consumers.feature_consumer import (
    CORR_SNAPSHOT_INTERVAL,
    CORR_WINDOW,
    EWMA_SPAN,
    FeatureWriter,
    SymbolWindows,
    WINDOW_SIZE,
    Z_SCORE_WINDOW,
    compute_correlations,
    compute_ewma_vol,
    compute_pca_residual,
    compute_z_score,
    warm_start as warm_windows,
)
from src.detection.anomaly_engine import ANOMALY_COOLDOWN_MINUTES, SYMBOLS, should_insert_event
from src.detection.macro_calendar import augment_description, macro_event_for
from src.detection.pipeline import DetectionPipeline
from src.producers.data_provider import MarketDataProvider


class SlimApp:
    """
    Composition root for the container-less profile.

    Dependencies are plain attributes so tests can inject fakes:
    provider, windows, writer, pipeline. The real __init__ wires the
    production objects and warm-starts from market_features.
    """

    def __init__(
        self,
        symbols=None,
        *,
        provider=None,
        writer=None,
        pipeline=None,
        windows=None,
    ):
        self.symbols = symbols or SYMBOLS
        self.stop_event = threading.Event()
        self.message_count = 0

        database_url = os.getenv("DATABASE_URL", "")
        if database_url == "" and writer is None:
            raise SystemExit("DATABASE_URL is required (e.g. in .env)")

        self.provider = provider or MarketDataProvider(self.symbols)
        self.windows = windows or SymbolWindows(self.symbols, maxlen=WINDOW_SIZE)
        self.writer = writer or FeatureWriter(database_url)
        self.pipeline = pipeline or DetectionPipeline()

        # Warm-start the feature windows AND every detector from DB
        # history so detection is fully active on the first tick.
        recent = self.writer.fetch_recent_features(self.symbols, limit=WINDOW_SIZE)
        counts = warm_windows(self.windows, lambda: iter(recent))
        closes: dict[str, list] = {}
        for row in recent:
            closes.setdefault(row["symbol"], []).append(
                (row["timestamp"], float(row["price"]))
            )
        detector_counts = self.pipeline.warm_start(prices_by_symbol=closes)

        logger.info(
            "[SLIM WARM-START] %d feature rows across %d symbols; detectors preloaded: %s",
            sum(counts.values()),
            len(counts),
            {k: v for k, v in detector_counts.items() if v},
        )

    # ------------------------------------------------------------------
    # Tick path: features -> storage -> detection -> alerts
    # ------------------------------------------------------------------

    def process_tick(self, quote: dict) -> None:
        symbol = quote.get("symbol", "UNKNOWN")
        price = quote.get("price")
        if price is None:
            return
        timestamp = quote.get("timestamp") or datetime.now(timezone.utc).isoformat()

        record = {
            "timestamp": timestamp,
            "symbol": symbol,
            "price": price,
            "return_1m": quote.get("return_1m"),
            "return_5m": quote.get("return_5m"),
            "volume": quote.get("volume"),
        }
        self.windows.append(symbol, record)

        returns = self.windows.get_returns(symbol)
        db_record = {
            **record,
            "z_score": compute_z_score(returns, window=Z_SCORE_WINDOW),
            "ewma_vol": compute_ewma_vol(returns, span=EWMA_SPAN),
            "pca_residual": compute_pca_residual(self.windows),
        }
        self.writer.write_feature(db_record)

        self.message_count += 1
        if self.message_count % CORR_SNAPSHOT_INTERVAL == 0:
            pairs = compute_correlations(self.windows, window=CORR_WINDOW)
            if pairs is not None:
                self.writer.write_correlation_snapshot(datetime.now(timezone.utc), pairs)

        self._detect_and_store(symbol, price, timestamp)

    def _detect_and_store(self, symbol: str, price, timestamp_str) -> None:
        ts = (
            datetime.fromisoformat(timestamp_str)
            if isinstance(timestamp_str, str)
            else timestamp_str
        )
        anomalies = self.pipeline.detect(asset=symbol, price=float(price), timestamp=ts)
        if not anomalies:
            return

        for anomaly in anomalies:
            score = round(float(anomaly["score"]), 3)
            last_ts, last_score = self._last_event(symbol)
            insert, reason = should_insert_event(score, last_ts, last_score, ts)
            if not insert:
                logger.info("[ANOMALY] %s score=%.3f suppressed: %s", symbol, score, reason)
                continue

            flags = {"z_flag": False, "ewma_flag": False, "pca_flag": False}
            flag_key = FLAG_BY_TYPE.get(anomaly["type"])
            if flag_key:
                flags[flag_key] = True

            # Macro-calendar context: annotate (never suppress) when the
            # tick coincides with a scheduled release like FOMC/CPI/NFP.
            description = build_description(symbol, anomaly)
            macro = macro_event_for(ts)
            if macro:
                description = augment_description(description, ts)

            self._persist_anomaly(
                (
                    ts,
                    symbol,
                    score,
                    flags["z_flag"],
                    flags["ewma_flag"],
                    flags["pca_flag"],
                    description,
                    macro["name"] if macro else None,
                )
            )
            logger.warning(
                "[ANOMALY] %s score=%.3f type=%s severity=%s",
                symbol, score, anomaly["type"], anomaly["severity"],
            )

    # ------------------------------------------------------------------
    # Storage hooks (overridden in tests; psycopg2 in production)
    # ------------------------------------------------------------------

    def _last_event(self, symbol: str):
        """Most recent event inside the cooldown window — sync twin of the
        consumer's async helper, same rule as the batch engine."""
        sql = """
            SELECT timestamp, anomaly_score FROM anomaly_events
            WHERE symbol = %s
              AND timestamp >= NOW() - (%s * INTERVAL '1 minute')
            ORDER BY timestamp DESC LIMIT 1
        """
        try:
            with self.writer._conn.cursor() as cur:
                cur.execute(sql, (symbol, ANOMALY_COOLDOWN_MINUTES))
                row = cur.fetchone()
        except Exception:
            self.writer._conn.rollback()
            return None, None
        if row is None:
            return None, None
        return row[0], float(row[1])

    def _persist_anomaly(self, params: tuple) -> None:
        sql = """
            INSERT INTO anomaly_events
                (timestamp, symbol, anomaly_score,
                 z_flag, ewma_flag, pca_flag, description, macro_context)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        with self.writer._conn.cursor() as cur:
            cur.execute(sql, params)
        self.writer._conn.commit()

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    def ingest_loop(self) -> None:
        logger.info(
            "[SLIM] Fetching %d symbols every %ds: %s",
            len(self.symbols), FETCH_INTERVAL, ", ".join(self.symbols),
        )
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                quotes = self.provider.fetch_all()
                fresh = 0
                for quote in quotes:
                    try:
                        self.process_tick(quote)
                        fresh += 1
                    except Exception:
                        logger.exception("[SLIM] tick failed for %s", quote.get("symbol"))
                logger.info("[SLIM] cycle: %d/%d fresh ticks", fresh, len(self.symbols))
            except Exception:
                logger.exception("[SLIM] fetch cycle failed")
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(1.0, FETCH_INTERVAL - elapsed))

    def batch_loop(self) -> None:
        from src.detection.scheduler import get_connection, run_detection_cycle

        logger.info("[SLIM] Batch detection every %ds", BATCH_INTERVAL)
        while not self.stop_event.is_set():
            if self.stop_event.wait(BATCH_INTERVAL):
                return
            try:
                conn = get_connection()
                try:
                    run_detection_cycle(conn)
                finally:
                    conn.close()
            except Exception:
                logger.exception("[SLIM] batch cycle failed")

    def daily_loop(self) -> None:
        from src.detection.scheduler import (
            BACKUP_HOUR_UTC,
            DAILY_CLEANUP_HOUR_UTC,
            get_connection,
            next_daily_time,
            run_backup,
            run_cleanup_cycle,
        )

        now = datetime.now(timezone.utc)
        next_cleanup = next_daily_time(now, DAILY_CLEANUP_HOUR_UTC)
        next_backup = next_daily_time(now, BACKUP_HOUR_UTC) if BACKUPS_ENABLED else None
        logger.info(
            "[SLIM] cleanup daily at %02d UTC; backups %s",
            DAILY_CLEANUP_HOUR_UTC, "enabled" if BACKUPS_ENABLED else "disabled",
        )

        while not self.stop_event.is_set():
            if self.stop_event.wait(60):
                return
            now = datetime.now(timezone.utc)
            if now >= next_cleanup:
                try:
                    conn = get_connection()
                    try:
                        run_cleanup_cycle(conn)
                    finally:
                        conn.close()
                except Exception:
                    logger.exception("[SLIM] cleanup failed")
                next_cleanup = next_daily_time(
                    now + timedelta(minutes=1), DAILY_CLEANUP_HOUR_UTC
                )
            if next_backup and now >= next_backup:
                try:
                    run_backup()
                except Exception:
                    logger.exception("[SLIM] backup failed")
                next_backup = next_daily_time(now + timedelta(minutes=1), BACKUP_HOUR_UTC)

    def run(self) -> None:
        import signal

        import uvicorn

        from src.api.main import app

        threading.Thread(target=self.ingest_loop, daemon=True, name="slim-ingest").start()
        threading.Thread(target=self.batch_loop, daemon=True, name="slim-batch").start()
        threading.Thread(target=self.daily_loop, daemon=True, name="slim-daily").start()

        def _stop(signum, frame):
            logger.info("[SLIM] shutdown signal received")
            self.stop_event.set()

        signal.signal(signal.SIGINT, _stop)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _stop)

        # FastAPI serves from the main thread; daemon threads die with it.
        logger.info("[SLIM] API listening on :%d", API_PORT)
        uvicorn.run(app, host="0.0.0.0", port=API_PORT, log_level="warning")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    SlimApp().run()


if __name__ == "__main__":
    main()
