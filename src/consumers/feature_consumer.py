#!/usr/bin/env python3
"""
Kafka consumer with real-time feature engineering for the anomaly detection pipeline.

Consumes raw market ticks from Kafka, maintains per-symbol rolling windows,
computes z-score, EWMA volatility, and PCA residual features, then persists
them to PostgreSQL. Also writes periodic correlation snapshots.

Usage:
    python -m src.consumers.feature_consumer
    DATABASE_URL=postgresql://postgres:password@localhost:5432/market_anomalies \
    KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
    python -m src.consumers.feature_consumer
"""

import json
import logging
import os
import signal
import sys
import time
from collections import deque
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Optional

import numpy as np
import pandas as pd
from kafka import KafkaConsumer
from kafka.errors import KafkaError
from psycopg2 import connect, DatabaseError
from psycopg2.extras import execute_values
from sklearn.decomposition import PCA
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/market_anomalies",
)
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "market-data")
CONSUMER_GROUP = os.getenv("CONSUMER_GROUP", "feature-engineering-group")

SYMBOLS = ["^GSPC", "^IXIC", "BTC-USD", "GC=F", "CL=F", "EURUSD=X", "^TNX"]

WINDOW_SIZE = 200       # Max data points kept per symbol
Z_SCORE_WINDOW = 20     # Rolling window for z-score
EWMA_SPAN = 12          # Span for EWMA volatility
PCA_WINDOW = 50         # Samples needed for PCA matrix
PCA_COMPONENTS = 2      # Number of principal components
CORR_WINDOW = 50        # Samples for correlation snapshots
CORR_SNAPSHOT_INTERVAL = 60  # Messages between correlation snapshots

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("feature_consumer")


def setup_logging() -> None:
    """Configure structured logging."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Graceful Shutdown
# ---------------------------------------------------------------------------

class ShutdownHandler:
    """Coordinate graceful shutdown on SIGINT/SIGTERM."""

    def __init__(self):
        self._requested = False
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, signum: int, frame: Any) -> None:
        name = signal.Signals(signum).name
        if self._requested:
            logger.warning("[SHUTDOWN] Second %s — forcing exit", name)
            sys.exit(1)
        self._requested = True
        logger.info("[SHUTDOWN] %s received, draining ...", name)

    @property
    def should_stop(self) -> bool:
        return self._requested


# ---------------------------------------------------------------------------
# Rolling Window Store
# ---------------------------------------------------------------------------

class SymbolWindows:
    """
    Per-symbol rolling deques storing the last N data points.

    Each entry is a dict with keys: timestamp, price, return_1m, volume.
    The deques auto-evict the oldest entries when maxlen is exceeded.
    """

    def __init__(self, symbols: list[str], maxlen: int = WINDOW_SIZE):
        self._windows: dict[str, deque] = {
            s: deque(maxlen=maxlen) for s in symbols
        }
        self._symbols = symbols

    def append(self, symbol: str, record: dict) -> None:
        """Add a data point to the symbol's window."""
        if symbol not in self._windows:
            self._windows[symbol] = deque(maxlen=WINDOW_SIZE)
        self._windows[symbol].append(record)

    def get(self, symbol: str) -> deque:
        """Return the deque for a symbol (empty deque if unknown)."""
        return self._windows.get(symbol, deque(maxlen=WINDOW_SIZE))

    def get_returns(self, symbol: str, n: Optional[int] = None) -> np.ndarray:
        """
        Extract return_1m values as a float64 array.

        Args:
            symbol: Asset symbol
            n: If set, return only the last n values

        Returns:
            numpy array of valid (non-None, non-NaN) returns
        """
        window = self._windows.get(symbol)
        if not window:
            return np.array([], dtype=np.float64)

        raw = [r["return_1m"] for r in window]
        # Filter out None values that occur when return couldn't be computed
        valid = [x for x in raw if x is not None]
        if not valid:
            return np.array([], dtype=np.float64)

        arr = np.array(valid, dtype=np.float64)
        # Replace any remaining NaN/inf with NaN so they can be filtered
        arr = arr[np.isfinite(arr)]

        if n is not None and len(arr) > n:
            arr = arr[-n:]
        return arr

    def get_return_matrix(self, n: int = PCA_WINDOW) -> Optional[np.ndarray]:
        """
        Build an (n x num_symbols) matrix of aligned returns.

        Each column is one symbol's last n valid return values.
        Returns None if any symbol has fewer than n valid returns.
        """
        columns = []
        for symbol in self._symbols:
            returns = self.get_returns(symbol, n=n)
            if len(returns) < n:
                return None
            columns.append(returns[-n:])

        # Stack into (n x num_symbols) — each row is one time step
        return np.column_stack(columns)

    def sizes(self) -> dict[str, int]:
        """Return current window sizes for all symbols."""
        return {s: len(d) for s, d in self._windows.items()}

    def all_have_minimum(self, n: int) -> bool:
        """Check if every tracked symbol has at least n valid returns."""
        return all(len(self.get_returns(s)) >= n for s in self._symbols)


# ---------------------------------------------------------------------------
# Feature Computation
# ---------------------------------------------------------------------------

def compute_z_score(returns: np.ndarray, window: int = Z_SCORE_WINDOW) -> Optional[float]:
    """
    Compute z-score of the latest return against a rolling window.

    z = (x - mean) / std   with ddof=1.
    Returns 0.0 when std == 0, None when insufficient data.
    """
    if len(returns) < window + 1:
        return None

    window_returns = returns[-(window + 1):-1]  # Exclude current value
    current = returns[-1]

    mean = np.mean(window_returns)
    std = np.std(window_returns, ddof=1)

    if std == 0:
        return 0.0

    z = (current - mean) / std
    return round(float(z), 4)


def compute_ewma_vol(returns: np.ndarray, span: int = EWMA_SPAN) -> Optional[float]:
    """
    Compute exponentially weighted standard deviation of returns.

    Uses pandas EWM with span parameter. Returns None when
    fewer than 2 data points are available.
    """
    if len(returns) < 2:
        return None

    series = pd.Series(returns)
    ewm_std = series.ewm(span=span, min_periods=2).std()
    result = ewm_std.iloc[-1]

    if pd.isna(result) or not np.isfinite(result):
        return None

    return round(float(result), 8)


def compute_pca_residual(
    windows: SymbolWindows,
    n_components: int = PCA_COMPONENTS,
    window: int = PCA_WINDOW,
) -> Optional[float]:
    """
    Compute PCA reconstruction residual for the latest observation.

    Steps:
        1. Build (n x 7) return matrix from all symbols' last n returns
        2. Fit PCA(n_components) on the full matrix
        3. Reconstruct: X_hat = PCA.inverse_transform(PCA.transform(X))
        4. Residual vector = X[-1] - X_hat[-1]
        5. Return L2 norm of the residual vector

    Returns None when any symbol has fewer than n data points.
    """
    matrix = windows.get_return_matrix(n=window)
    if matrix is None:
        return None

    try:
        pca = PCA(n_components=n_components)
        X_reduced = pca.fit_transform(matrix)
        X_reconstructed = pca.inverse_transform(X_reduced)

        # Residual for the latest row (all 7 symbols)
        residual_vector = matrix[-1] - X_reconstructed[-1]
        l2_norm = float(np.linalg.norm(residual_vector))

        if not np.isfinite(l2_norm):
            return None

        return round(l2_norm, 4)

    except Exception as e:
        logger.warning("[PCA] Computation failed: %s", e)
        return None


def compute_correlations(
    windows: SymbolWindows,
    window: int = CORR_WINDOW,
) -> Optional[list[tuple[str, str, float]]]:
    """
    Compute pairwise Pearson correlations from the last n returns.

    Returns a list of (symbol_a, symbol_b, correlation) tuples for all
    unique pairs. Returns None if any symbol lacks sufficient data.
    """
    if not windows.all_have_minimum(window):
        return None

    # Build aligned return series for all symbols
    return_dict: dict[str, np.ndarray] = {}
    for symbol in SYMBOLS:
        returns = windows.get_returns(symbol, n=window)
        if len(returns) < window:
            return None
        return_dict[symbol] = returns[-window:]

    # Build DataFrame for easy correlation computation
    df = pd.DataFrame(return_dict)

    pairs = []
    for sym_a, sym_b in combinations(SYMBOLS, 2):
        corr = df[sym_a].corr(df[sym_b])
        if pd.isna(corr) or not np.isfinite(corr):
            continue
        pairs.append((sym_a, sym_b, round(float(corr), 4)))

    return pairs


# ---------------------------------------------------------------------------
# Database Operations
# ---------------------------------------------------------------------------

class FeatureWriter:
    """Handles all database writes with connection management."""

    def __init__(self, database_url: str):
        self._url = database_url
        self._conn = None
        self._reconnect()

    def _reconnect(self) -> None:
        """Establish or re-establish a database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        try:
            self._conn = connect(self._url)
            self._conn.autocommit = False
            logger.info("[DB] Connected to PostgreSQL")
        except DatabaseError as e:
            logger.error("[DB] Connection failed: %s", e)
            raise

    def write_feature(self, record: dict) -> bool:
        """
        Insert a single market_features row.

        Returns True on success, False on failure (logs and rolls back).
        """
        sql = """
            INSERT INTO market_features
                (timestamp, symbol, price, return_1m, return_5m,
                 z_score, ewma_vol, pca_residual, volume)
            VALUES
                (%(timestamp)s, %(symbol)s, %(price)s, %(return_1m)s,
                 %(return_5m)s, %(z_score)s, %(ewma_vol)s,
                 %(pca_residual)s, %(volume)s)
        """
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, record)
            self._conn.commit()
            return True
        except DatabaseError as e:
            self._conn.rollback()
            logger.error(
                "[DB] Failed to write feature for %s at %s: %s",
                record.get("symbol"),
                record.get("timestamp"),
                e,
            )
            return False
        except Exception as e:
            self._conn.rollback()
            logger.error("[DB] Unexpected error writing feature: %s", e, exc_info=True)
            return False

    def write_correlation_snapshot(
        self,
        timestamp: datetime,
        pairs: list[tuple[str, str, float]],
    ) -> bool:
        """
        Insert correlation snapshot rows for all pairs.

        Returns True on success, False on failure.
        """
        if not pairs:
            return True

        rows = [(timestamp, sym_a, sym_b, corr) for sym_a, sym_b, corr in pairs]

        sql = """
            INSERT INTO correlation_snapshots
                (timestamp, symbol_a, symbol_b, correlation)
            VALUES %s
            ON CONFLICT (timestamp, symbol_a, symbol_b) DO NOTHING
        """
        try:
            with self._conn.cursor() as cur:
                execute_values(cur, sql, rows, page_size=100)
            self._conn.commit()
            logger.info(
                "[DB] Wrote %d correlation snapshot pairs at %s",
                len(pairs),
                timestamp.isoformat(),
            )
            return True
        except DatabaseError as e:
            self._conn.rollback()
            logger.error("[DB] Failed to write correlation snapshot: %s", e)
            return False
        except Exception as e:
            self._conn.rollback()
            logger.error(
                "[DB] Unexpected error writing correlation snapshot: %s",
                e,
                exc_info=True,
            )
            return False

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
                logger.info("[DB] Connection closed")
            except Exception:
                pass
            self._conn = None


# ---------------------------------------------------------------------------
# Kafka Consumer Factory
# ---------------------------------------------------------------------------

def create_consumer(
    bootstrap_servers: str,
    topic: str,
    group_id: str,
) -> KafkaConsumer:
    """Create and return a configured KafkaConsumer."""
    logger.info(
        "[KAFKA] Creating consumer: servers=%s topic=%s group=%s",
        bootstrap_servers,
        topic,
        group_id,
    )
    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        group_id=group_id,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        key_deserializer=lambda k: k.decode("utf-8") if k else None,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        auto_commit_interval_ms=5000,
        max_poll_records=500,
        session_timeout_ms=30000,
        heartbeat_interval_ms=10000,
        consumer_timeout_ms=1000,  # Poll timeout so we can check shutdown
    )
    logger.info("[KAFKA] Consumer created and subscribed")
    return consumer


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

def run() -> None:
    """
    Main consumer loop.

    For each message:
        1. Append to rolling window
        2. Compute z_score, ewma_vol, pca_residual
        3. Write to market_features
        4. Every CORR_SNAPSHOT_INTERVAL messages, write correlation_snapshots
    """
    setup_logging()
    shutdown = ShutdownHandler()

    logger.info("=" * 64)
    logger.info("  Feature Engineering Consumer — Starting")
    logger.info("  Kafka:   %s", KAFKA_BOOTSTRAP_SERVERS)
    logger.info("  Topic:   %s", KAFKA_TOPIC)
    logger.info("  Group:   %s", CONSUMER_GROUP)
    logger.info("  DB:      %s", DATABASE_URL.split("@")[-1])
    logger.info("=" * 64)

    # Initialize components
    windows = SymbolWindows(SYMBOLS, maxlen=WINDOW_SIZE)
    writer = FeatureWriter(DATABASE_URL)
    consumer = create_consumer(KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC, CONSUMER_GROUP)

    message_count = 0
    write_success = 0
    write_fail = 0
    pca_computations = 0
    pca_skips = 0
    corr_writes = 0

    try:
        while not shutdown.should_stop:
            # Poll with timeout so we can check shutdown flag
            try:
                records = consumer.poll(timeout_ms=1000)
            except KafkaError as e:
                logger.error("[KAFKA] Poll error: %s", e)
                time.sleep(2)
                continue

            if not records:
                continue

            for topic_partition, messages in records.items():
                for msg in messages:
                    if shutdown.should_stop:
                        break

                    message_count += 1
                    value = msg.value
                    symbol = value.get("symbol", "UNKNOWN")

                    # --- Append to rolling window ---
                    windows.append(symbol, {
                        "timestamp": value.get("timestamp"),
                        "price": value.get("price"),
                        "return_1m": value.get("return_1m"),
                        "return_5m": value.get("return_5m"),
                        "volume": value.get("volume"),
                        "fetch_id": value.get("fetch_id"),
                    })

                    # --- Compute features ---
                    returns = windows.get_returns(symbol)
                    z_score = compute_z_score(returns, window=Z_SCORE_WINDOW)
                    ewma_vol = compute_ewma_vol(returns, span=EWMA_SPAN)
                    pca_residual = compute_pca_residual(windows)

                    if pca_residual is not None:
                        pca_computations += 1
                    else:
                        pca_skips += 1

                    # --- Build DB record ---
                    db_record = {
                        "timestamp": value.get("timestamp"),
                        "symbol": symbol,
                        "price": value.get("price"),
                        "return_1m": value.get("return_1m"),
                        "return_5m": value.get("return_5m"),
                        "z_score": z_score,
                        "ewma_vol": ewma_vol,
                        "pca_residual": pca_residual,
                        "volume": value.get("volume"),
                    }

                    # --- Write to database ---
                    if writer.write_feature(db_record):
                        write_success += 1
                    else:
                        write_fail += 1

                    # --- Periodic correlation snapshot ---
                    if message_count % CORR_SNAPSHOT_INTERVAL == 0:
                        now = datetime.now(timezone.utc)
                        pairs = compute_correlations(windows, window=CORR_WINDOW)
                        if pairs is not None:
                            if writer.write_correlation_snapshot(now, pairs):
                                corr_writes += 1
                        else:
                            logger.debug(
                                "[CORR] Skipped snapshot at msg %d: "
                                "insufficient data for all symbols",
                                message_count,
                            )

                    # --- Progress log every 10 messages ---
                    if message_count % 10 == 0:
                        z_str = f"{z_score:.2f}" if z_score is not None else "N/A"
                        ewma_str = (
                            f"{ewma_vol:.6f}" if ewma_vol is not None else "N/A"
                        )
                        pca_str = (
                            f"{pca_residual:.4f}"
                            if pca_residual is not None
                            else "N/A"
                        )
                        logger.info(
                            "[CONSUMER] Processed %d messages. Latest: %s z=%s ewma=%s pca=%s",
                            message_count,
                            symbol,
                            z_str,
                            ewma_str,
                            pca_str,
                        )

    except KeyboardInterrupt:
        logger.info("[SHUTDOWN] Keyboard interrupt")
    except Exception as e:
        logger.critical("[FATAL] Unhandled exception: %s", e, exc_info=True)
        raise
    finally:
        # --- Final summary ---
        logger.info("=" * 64)
        logger.info("  Feature Consumer — Shutdown Summary")
        logger.info("  Messages consumed:    %d", message_count)
        logger.info("  Features written:     %d", write_success)
        logger.info("  Features failed:      %d", write_fail)
        logger.info("  PCA computations:     %d", pca_computations)
        logger.info("  PCA skipped (data):   %d", pca_skips)
        logger.info("  Correlation snapshots: %d", corr_writes)
        logger.info("  Window sizes:         %s", windows.sizes())
        logger.info("=" * 64)

        # --- Cleanup ---
        logger.info("[SHUTDOWN] Closing Kafka consumer ...")
        try:
            consumer.close()
        except Exception:
            pass

        logger.info("[SHUTDOWN] Closing database connection ...")
        writer.close()

        logger.info("[SHUTDOWN] Done")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run()