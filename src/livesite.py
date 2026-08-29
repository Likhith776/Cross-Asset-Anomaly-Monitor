"""
Live-site orchestrator: runs the full pipeline on CI hardware with no
database, persisting all state as JSON files.

Each run (GitHub Actions, every 30 min):
  1. loads previous state (feature history + anomaly log) from disk
  2. warm-starts windows + every detector from that state
  3. fetches fresh quotes and runs the standard tick path
     (features -> DetectionPipeline -> cooldown-filtered alerts)
  4. publishes dashboard artifacts: latest.json, anomalies.json,
     charts/{symbol}.json alongside the static index.html

State and artifacts live in one output tree that the workflow pushes to
the `live-data` branch, which GitHub Pages serves. Nothing else is
needed: no Postgres, no broker, no always-on process.

The tick path is SlimApp's own — this module only swaps its storage for
file-backed equivalents.
"""

import json
import logging
import os
import re
from typing import Optional
from datetime import datetime, timedelta, timezone

from src.detection.anomaly_engine import SYMBOLS
from src.producers.data_provider import MarketDataProvider
from src.slim import SlimApp
from src.universe import load_universe

logger = logging.getLogger("livesite")

HISTORY_CAP_PER_SYMBOL = 2000   # ~40 days at a 30-min cadence
ANOMALIES_CAP = 500
CHART_MAX_POINTS = 600
FRESHNESS = {"live": 120, "delayed": 900}  # seconds -> classification bounds

SCHEMA_VERSION = 1


class FileStore:
    """
    Writer-compatible store backed by plain lists, persisted as JSON.

    Satisfies the surface SlimApp expects from FeatureWriter:
    write_feature / write_correlation_snapshot / fetch_recent_features,
    plus an append-only anomaly log used by the cooldown hooks.

    Feedback is keyed by (symbol, timestamp) — the static site has no
    real auth/session model, so feedback there is best-effort. See
    docs/DATA_CONTRACT.md for the limitation note.
    """

    def __init__(self, features=None, anomalies=None, feedback=None):
        self.features = list(features or [])      # oldest-first per symbol
        self.anomalies = list(anomalies or [])    # newest-first
        self.correlation_snapshots = []
        self.feedback = list(feedback or [])      # newest-first

    # --- FeatureWriter-compatible surface -----------------------------

    def write_feature(self, record) -> bool:
        self.features.append(dict(record))
        return True

    def write_correlation_snapshot(self, timestamp, pairs) -> bool:
        # Cap snapshots so the state file doesn't grow unbounded.
        self.correlation_snapshots.append(
            {"timestamp": timestamp.isoformat(), "pairs": pairs}
        )
        if len(self.correlation_snapshots) > 200:
            self.correlation_snapshots = self.correlation_snapshots[-200:]
        return True

    def record_feedback(
        self,
        symbol: str,
        timestamp: str,
        label: str,
        note: str | None = None,
    ) -> dict:
        """
        Record a label keyed by (symbol, timestamp). The most recent
        record wins, so calling this twice for the same pair replaces
        the prior label. Returns the new entry.
        """
        entry = {
            "symbol": symbol,
            "timestamp": timestamp,
            "label": label,
            "noted_at": datetime.now(timezone.utc).isoformat(),
            "note": note,
        }
        # Drop any prior label for the same (symbol, timestamp).
        self.feedback = [
            f for f in self.feedback
            if not (f["symbol"] == symbol and f["timestamp"] == timestamp)
        ]
        self.feedback.insert(0, entry)
        # Cap to 500 entries so the published file stays small.
        del self.feedback[500:]
        return entry

    @staticmethod
    def latest_label_for(entries: list[dict], symbol: str, timestamp: str):
        for e in entries:
            if e.get("symbol") == symbol and e.get("timestamp") == timestamp:
                return e
        return None

    def fetch_recent_features(self, symbols, limit):
        rows = [r for r in self.features if r.get("symbol") in set(symbols)]
        rows.sort(key=lambda r: r["timestamp"])
        out = []
        for symbol in symbols:
            per_sym = [r for r in rows if r["symbol"] == symbol][-limit:]
            out.extend(per_sym)
        return out

    # --- FeatureWriter-compatible surface -----------------------------

    def write_feature(self, record) -> bool:
        self.features.append(dict(record))
        return True

    def write_correlation_snapshot(self, timestamp, pairs) -> bool:
        # Cap snapshots so the state file doesn't grow unbounded.
        self.correlation_snapshots.append(
            {"timestamp": timestamp.isoformat(), "pairs": pairs}
        )
        if len(self.correlation_snapshots) > 200:
            self.correlation_snapshots = self.correlation_snapshots[-200:]
        return True

    def fetch_recent_features(self, symbols, limit):
        rows = [r for r in self.features if r.get("symbol") in set(symbols)]
        rows.sort(key=lambda r: r["timestamp"])
        out = []
        for symbol in symbols:
            per_sym = [r for r in rows if r["symbol"] == symbol][-limit:]
            out.extend(per_sym)
        return out


class LiveSite:
    """One scheduled run of the pipeline against file-backed storage."""

    def __init__(self, symbols, provider, state_dir, out_dir):
        self.state_dir = state_dir
        self.out_dir = out_dir
        self.symbols = symbols or SYMBOLS

        features, anomalies, feedback = self._load_state()
        self.store = FileStore(features, anomalies, feedback)

        self.provider = provider or MarketDataProvider(self.symbols)
        self.app = SlimApp(
            symbols=self.symbols, provider=self.provider, writer=self.store
        )
        # Route cooldown/persistence hooks at the anomaly log instead of SQL.
        self.app._last_event = self._last_event
        self.app._persist_anomaly = self._persist_anomaly

        # Seed at least one correlation snapshot on the first run so the
        # heatmap renders something useful right away, instead of waiting
        # for 60 live ticks to accumulate. SlimApp's __init__ has already
        # warm-started the windows from the carried-over feature history
        # by this point, so compute_correlations sees real data.
        if not self.store.correlation_snapshots and self.store.features:
            from src.consumers.feature_consumer import compute_correlations
            pairs = compute_correlations(self.app.windows)
            if pairs:
                self.store.write_correlation_snapshot(
                    datetime.now(timezone.utc), pairs
                )
                logger.info(
                    "[LIVE] seeded %d correlation pairs from historical data",
                    len(pairs),
                )

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self):
        # State travels inside the published tree: <branch>/state/*.json
        base = os.path.join(self.state_dir, "state")
        history_path = os.path.join(base, "history.json")
        anomalies_path = os.path.join(base, "anomalies.json")
        feedback_path = os.path.join(base, "feedback.json")
        try:
            with open(history_path, encoding="utf-8") as fh:
                features = json.load(fh).get("features", [])
        except (OSError, ValueError):
            features = []
            logger.info("[LIVE] no previous feature history — cold start")
        try:
            with open(anomalies_path, encoding="utf-8") as fh:
                anomalies = json.load(fh).get("events", [])
        except (OSError, ValueError):
            anomalies = []
        try:
            with open(feedback_path, encoding="utf-8") as fh:
                feedback = json.load(fh).get("feedback", [])
        except (OSError, ValueError):
            feedback = []

        # Older versions of the seeder wrote rows without return_1m,
        # which the windows need for compute_correlations. Patch
        # adjacent returns in place before any window sees the data.
        features = self._enrich_returns(features)

        # Reseed whenever the carried-over state is too small to draw
        # a chart from — once a branch has at least 100 rows, the
        # accumulating ticks take over. On the first-ever branch run
        # this is also true (state was empty/just a probe). Logs are
        # verbose by design: a silent fail here is what kept the
        # dashboard empty for the first few LIVE cycles.
        if (
            len(features) < 100
            and not os.environ.get("SKIP_HISTORICAL_SEED")
        ):
            from src.livesite_seed import seed_history

            logger.info(
                "[LIVE] carried state has %d rows (< 100); running historical seed",
                len(features),
            )
            seed_rows = seed_history(self.symbols, periods=180, interval="1h")
            if seed_rows:
                features = self._enrich_returns(seed_rows)
                logger.info(
                    "[LIVE] historical seed wrote %d rows; warm-start from full data",
                    len(features),
                )
            else:
                logger.warning(
                    "[LIVE] historical seed returned 0 rows; "
                    "continuing with carried-over state (%d rows)",
                    len(features),
                )
        logger.info(
            "[LIVE] loaded %d historical ticks, %d past anomalies, %d feedback entries",
            len(features), len(anomalies), len(feedback),
        )
        return features, anomalies, feedback
        # State travels inside the published tree: <branch>/state/*.json
        base = os.path.join(self.state_dir, "state")
        history_path = os.path.join(base, "history.json")
        anomalies_path = os.path.join(base, "anomalies.json")
        try:
            with open(history_path, encoding="utf-8") as fh:
                features = json.load(fh).get("features", [])
        except (OSError, ValueError):
            features = []
            logger.info("[LIVE] no previous feature history — cold start")
        try:
            with open(anomalies_path, encoding="utf-8") as fh:
                anomalies = json.load(fh).get("events", [])
        except (OSError, ValueError):
            anomalies = []

        # Older versions of the seeder wrote rows without return_1m,
        # which the windows need for compute_correlations. Patch
        # adjacent returns in place before any window sees the data.
        features = self._enrich_returns(features)

        # Reseed whenever the carried-over state is too small to draw
        # a chart from — once a branch has at least 100 rows, the
        # accumulating ticks take over. On the first-ever branch run
        # this is also true (state was empty/just a probe). Logs are
        # verbose by design: a silent fail here is what kept the
        # dashboard empty for the first few LIVE cycles.
        if (
            len(features) < 100
            and not os.environ.get("SKIP_HISTORICAL_SEED")
        ):
            from src.livesite_seed import seed_history

            logger.info(
                "[LIVE] carried state has %d rows (< 100); running historical seed",
                len(features),
            )
            seed_rows = seed_history(self.symbols, periods=180, interval="1h")
            if seed_rows:
                features = self._enrich_returns(seed_rows)
                logger.info(
                    "[LIVE] historical seed wrote %d rows; warm-start from full data",
                    len(features),
                )
            else:
                logger.warning(
                    "[LIVE] historical seed returned 0 rows; "
                    "continuing with carried-over state (%d rows)",
                    len(features),
                )
        logger.info(
            "[LIVE] loaded %d historical ticks, %d past anomalies",
            len(features), len(anomalies),
        )
        return features, anomalies

    @staticmethod
    def _enrich_returns(features: list[dict]) -> list[dict]:
        """Compute return_1m from adjacent prices for any row that lacks it.

        Older published states were written before the seeder enriched
        return fields; downstream code (compute_correlations, the windows
        that build features) needs them to be present.
        """
        if not features:
            return features
        by_symbol: dict[str, list[dict]] = {}
        for row in features:
            by_symbol.setdefault(row.get("symbol"), []).append(row)
        enriched: list[dict] = []
        for _, rows in by_symbol.items():
            for i, row in enumerate(rows):
                if row.get("return_1m") is None and i > 0:
                    prev = rows[i - 1].get("price")
                    cur = row.get("price")
                    if prev and cur:
                        row["return_1m"] = (cur - prev) / prev
                enriched.append(row)
        enriched.sort(key=lambda r: (r.get("symbol"), r.get("timestamp")))
        return enriched

    def save_state(self):
        # Written into the artifact tree so pushing the site persists state
        # for the next run (the workflow amends one commit on live-data).
        base = os.path.join(self.out_dir, "state")
        os.makedirs(base, exist_ok=True)
        capped = {}
        for row in self.store.features:
            capped.setdefault(row.get("symbol"), []).append(row)
        trimmed = []
        for sym, rows in capped.items():
            trimmed.extend(rows[-HISTORY_CAP_PER_SYMBOL:])
        trimmed.sort(key=lambda r: (r.get("symbol"), r.get("timestamp")))
        self._write_json(
            os.path.join(base, "history.json"),
            {"schema": SCHEMA_VERSION, "features": trimmed},
        )
        self._write_json(
            os.path.join(base, "anomalies.json"),
            {"schema": SCHEMA_VERSION, "events": self.store.anomalies[:ANOMALIES_CAP]},
        )
        self._write_json(
            os.path.join(base, "feedback.json"),
            {"schema": SCHEMA_VERSION, "feedback": self.store.feedback[:500]},
        )

    @staticmethod
    def _write_json(path, payload):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))

    # ------------------------------------------------------------------
    # Cooldown hooks backed by the anomaly log
    # ------------------------------------------------------------------

    def _last_event(self, symbol: str):
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=ANOMALY_COOLDOWN_MINUTES())
        for event in self.store.anomalies:
            if event["symbol"] != symbol:
                continue
            event_ts = datetime.fromisoformat(event["timestamp"])
            if event_ts >= cutoff:
                return event_ts, float(event["score"])
            break  # newest-first: first older-than-cooldown ends the scan
        return None, None

    def _persist_anomaly(self, params: tuple) -> None:
        ts, symbol, score, z, ewma, pca, description, macro_context = params
        self.store.anomalies.insert(0, {
            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "symbol": symbol,
            "score": float(score),
            "description": description,
            "macro_context": macro_context,
        })
        del self.store.anomalies[ANOMALIES_CAP:]

    @staticmethod
    def _extract_severity(description: str) -> str:
        """Pull the trailing '— <sev> severity' from a description."""
        m = re.search(r"—\s+(\w+)\s+severity", description or "")
        return m.group(1) if m else "low"

    @staticmethod
    def _extract_regime(description: str) -> Optional[str]:
        """Pull the volatility regime from a '[regime: X]' marker."""
        m = re.search(r"\[regime:\s*(\w+)\]", description or "")
        return m.group(1) if m else None

    # ------------------------------------------------------------------
    # Run one cycle and publish artifacts
    # ------------------------------------------------------------------

    def run_cycle(self) -> int:
        quotes = self.provider.fetch_all()
        fresh = 0
        for quote in quotes:
            try:
                self.app.process_tick(quote)
                fresh += 1
            except Exception:
                logger.exception("[LIVE] tick failed for %s", quote.get("symbol"))
        logger.info("[LIVE] cycle complete: %d/%d fresh ticks", fresh, len(quotes))
        return fresh

    def build_artifacts(self) -> None:
        now = datetime.now(timezone.utc)
        generated_at = now.isoformat()

        latest_symbols = {}
        labels = load_universe().labels
        tracker = self.app.pipeline.regime_tracker

        def site_regime(symbol: str) -> str:
            return tracker.current_regime(symbol)

        for symbol in self.symbols:
            rows = [r for r in self.store.features if r.get("symbol") == symbol]
            if not rows:
                continue
            last = rows[-1]
            age = (
                now - datetime.fromisoformat(str(last["timestamp"]))
            ).total_seconds()
            freshness = "live" if age <= FRESHNESS["live"] else (
                "delayed" if age <= FRESHNESS["delayed"] else "stale"
            )
            latest_symbols[symbol] = {
                "label": labels.get(symbol, symbol),
                "price": last.get("price"),
                # Current volatility regime from the pipeline's tracker
                # (visible proof the regime classifier is running).
                "regime": site_regime(symbol),
                "return_1m": last.get("return_1m"),
                "return_5m": last.get("return_5m"),
                "volume": last.get("volume"),
                "source": last.get("source", "yfinance"),
                "timestamp": str(last["timestamp"]),
                "freshness": freshness,
                "age_seconds": round(age),
            }

        self._write_json(
            os.path.join(self.out_dir, "data", "latest.json"),
            {"schema": SCHEMA_VERSION, "generated_at": generated_at,
             "symbols": latest_symbols},
        )
        self._write_json(
            os.path.join(self.out_dir, "data", "anomalies.json"),
            {"schema": SCHEMA_VERSION, "generated_at": generated_at,
             "events": [
                 {**{k: e.get(k) for k in ("timestamp", "symbol", "score", "description")},
                  "severity": self._extract_severity(e.get("description", "")),
                  "regime": self._extract_regime(e.get("description", "")),
                  "macro_context": e.get("macro_context")}
                 for e in self.store.anomalies[:200]
             ]},
        )

        # Correlations: most recent snapshot as a matrix + history tail
        # so the timeline can show correlation drift over time.
        matrix_symbols = self.symbols
        matrix_size = len(matrix_symbols)
        latest_pairs: list[dict] = []
        for snap in reversed(self.store.correlation_snapshots):
            if snap.get("pairs"):
                latest_pairs = snap["pairs"]
                break
        correlation_matrix = [[None] * matrix_size for _ in range(matrix_size)]
        for i in range(matrix_size):
            correlation_matrix[i][i] = 1.0
        for sym_a, sym_b, corr in latest_pairs:
            if sym_a in matrix_symbols and sym_b in matrix_symbols:
                i, j = matrix_symbols.index(sym_a), matrix_symbols.index(sym_b)
                correlation_matrix[i][j] = corr
                correlation_matrix[j][i] = corr
        self._write_json(
            os.path.join(self.out_dir, "data", "correlations.json"),
            {
                "schema": SCHEMA_VERSION,
                "generated_at": generated_at,
                "symbols": list(matrix_symbols),
                "matrix": correlation_matrix,
                "history": [
                    {"timestamp": s["timestamp"], "pairs": s["pairs"]}
                    for s in self.store.correlation_snapshots[-50:]
                ],
            },
        )

        # Feedback: most-recent-wins per (symbol, timestamp). The
        # description that the detector attribution needs is read from
        # the matching anomaly_events row.
        label_to_description = {
            (a["symbol"], a["timestamp"]): a.get("description", "")
            for a in self.store.anomalies
        }
        feedback_rows: list[dict] = []
        for entry in self.store.feedback:
            row = {
                "anomaly_event_id": None,
                "symbol": entry.get("symbol"),
                "timestamp": entry.get("timestamp"),
                "label": entry.get("label"),
                "noted_at": entry.get("noted_at"),
                "description": label_to_description.get(
                    (entry.get("symbol"), entry.get("timestamp")), ""
                ),
            }
            feedback_rows.append(row)
        self._write_json(
            os.path.join(self.out_dir, "data", "feedback.json"),
            {"schema": SCHEMA_VERSION, "generated_at": generated_at,
             "feedback": feedback_rows},
        )

        from src.precision import compute_precision

        self._write_json(
            os.path.join(self.out_dir, "data", "precision.json"),
            {
                "schema": SCHEMA_VERSION,
                "generated_at": generated_at,
                **compute_precision(feedback_rows, window_days=30),
            },
        )

        for symbol in self.symbols:
            points = [
                [str(r["timestamp"]), r["price"]]
                for r in self.store.features
                if r.get("symbol") == symbol and r.get("price") is not None
            ]
            self._write_json(
                os.path.join(self.out_dir, "data", "charts", f"{_safe(symbol)}.json"),
                {
                    "schema": SCHEMA_VERSION,
                    "symbol": symbol,
                    "points": _decimate(points, CHART_MAX_POINTS),
                },
            )

        # Copy the static dashboard next to the data.
        template = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "site", "index.html",
        )
        with open(template, encoding="utf-8") as src:
            html = src.read()
        out_index = os.path.join(self.out_dir, "index.html")
        os.makedirs(self.out_dir, exist_ok=True)
        with open(out_index, "w", encoding="utf-8") as dst:
            dst.write(html)

        open(os.path.join(self.out_dir, ".nojekyll"), "w").close()
        logger.info("[LIVE] artifacts written to %s", self.out_dir)


def ANOMALY_COOLDOWN_MINUTES():
    from src.detection.anomaly_engine import ANOMALY_COOLDOWN_MINUTES as m
    return m


def _safe(symbol: str) -> str:
    return symbol.replace("^", "idx_").replace("=", "_")


def _decimate(points, max_points):
    """Stride-based downsampling that keeps endpoints."""
    if len(points) <= max_points:
        return points
    stride = len(points) / max_points
    keep = [points[int(i * stride)] for i in range(max_points - 1)]
    keep.append(points[-1])
    return keep


def publish(state_dir: str, out_dir: str, symbols=None, provider=None,
            cycles: int = 1) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if provider is None and os.getenv("LIVE_PROVIDER", "composite") == "composite":
        # GitHub runners (US) are geo-blocked by Binance (HTTP 451) and
        # silently 0-quoted by Yahoo's anti-bot walls, so the hosted
        # profile layers keyless Coinbase (US exchange, no geo-block) over
        # a retried yfinance pass for everything else. Coverage gaps are
        # visible on the dashboard as stale/delayed badges, not hidden.
        # Keyless primaries first: Coinbase for crypto (US exchange, no
        # geo-block), OpenExchangeRates for daily FX. Retried yfinance
        # then fills any remaining symbol when it isn't being throttled.
        from src.producers.coinbase_provider import CoinbaseProvider
        from src.producers.composite_provider import CompositeProvider
        from src.producers.openexchangerates_provider import OpenExchangeRatesProvider

        resolved = symbols or SYMBOLS
        provider = CompositeProvider(
            symbols=resolved,
            primaries=[
                CoinbaseProvider(resolved),
                OpenExchangeRatesProvider(resolved),
            ],
            secondary=MarketDataProvider(resolved),
            attempts=int(os.getenv("LIVE_SECONDARY_ATTEMPTS", "2")),
            retry_wait=int(os.getenv("LIVE_SECONDARY_WAIT", "20")),
        )
        symbols = resolved
    site = LiveSite(symbols, provider, state_dir, out_dir)
    for _ in range(cycles):
        site.run_cycle()
    site.save_state()
    site.build_artifacts()
