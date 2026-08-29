#!/usr/bin/env python3
"""
Anomaly scoring engine for the Cross-Asset Market Anomaly Detection System.

Reads pre-computed features from market_features, applies three weighted
detectors (Z-Score, EWMA Volatility Spike, PCA Residual), computes a
composite anomaly score, and inserts qualifying events into anomaly_events.

Designed to be called by Airflow on a 5-minute schedule, but also
runnable standalone for testing and development.

Usage:
    python src/detection/anomaly_engine.py
    DATABASE_URL=postgresql://... python src/detection/anomaly_engine.py
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

from src.detection.lead_lag import augment_description, lead_lag_for
from src.detection.regime import (
    REGIME_MEDIUM,
    classify_vol_percentile,
    scale_for_regime,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/market_anomalies",
)

# Universe (symbols) comes from config/universe.json — single source of
# truth shared by every entry point. Edited only in that file.
from src.universe import load_universe  # noqa: E402

SYMBOLS: list[str] = list(load_universe().symbols)

FEATURE_WINDOW = 100  # Rows to query per symbol

# Detector weights — must sum to 1.0
W_Z_SCORE = 0.35
W_EWMA_VOL = 0.35
W_PCA_RESIDUAL = 0.30

# Detector thresholds
Z_SCORE_THRESHOLD = 2.5
Z_SCORE_NORMALIZER = 4.0

EWMA_MULTIPLIER = 2.0
EWMA_NORMALIZER = 3.0

PCA_Z_THRESHOLD = 2.8
PCA_NORMALIZER = 5.0

# Score threshold for event insertion
SCORE_INSERT_THRESHOLD = 0.30

# Sustained-anomaly suppression: once an event is recorded for a symbol,
# re-insertion is suppressed for the cooldown window unless the score
# escalates by at least ANOMALY_ESCALATION_DELTA. Without this, a single
# sustained anomaly would insert a near-identical event every cycle.
ANOMALY_COOLDOWN_MINUTES = 30
ANOMALY_ESCALATION_DELTA = 0.10

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("anomaly_engine")


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
# Data Access
# ---------------------------------------------------------------------------

def fetch_features(
    cur: Any,
    symbol: str,
    window: int = FEATURE_WINDOW,
) -> list[dict[str, Any]]:
    """
    Query the last N rows of market_features for a given symbol.

    Returns a list of dicts, ordered by timestamp ASC (oldest first)
    so that index [-1] is always the most recent row.
    """
    cur.execute(
        """
        SELECT
            timestamp,
            symbol,
            price,
            return_1m,
            return_5m,
            z_score,
            ewma_vol,
            pca_residual,
            volume
        FROM market_features
        WHERE symbol = %s
        ORDER BY timestamp DESC
        LIMIT %s
        """,
        (symbol, window),
    )
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    records = [dict(zip(columns, row)) for row in rows]
    # Reverse so index 0 = oldest, index -1 = most recent
    records.reverse()
    return records


def should_insert_event(
    new_score: float,
    last_timestamp: Optional[datetime],
    last_score: Optional[float],
    now: datetime,
    cooldown_minutes: int = ANOMALY_COOLDOWN_MINUTES,
    escalation_delta: float = ANOMALY_ESCALATION_DELTA,
) -> tuple[bool, str]:
    """
    Pure decision for sustained-anomaly suppression.

    Returns (insert?, reason). An event is inserted when:
      - there is no recent event for the symbol, or
      - the cooldown window has elapsed, or
      - the score escalated by at least escalation_delta.
    """
    if last_timestamp is None:
        return True, "first event for symbol in cooldown window"

    age = now - last_timestamp
    if age >= timedelta(minutes=cooldown_minutes):
        return True, f"cooldown elapsed ({age.total_seconds() / 60:.0f}m since last event)"

    if last_score is not None and new_score >= float(last_score) + escalation_delta:
        return True, f"score escalated ({float(last_score):.3f} -> {new_score:.3f})"

    last_score_str = f"{float(last_score):.3f}" if last_score is not None else "n/a"
    return False, (
        f"sustained anomaly (score {new_score:.3f} vs last {last_score_str} "
        f"{age.total_seconds() / 60:.0f}m ago)"
    )


def apply_cooldown(
    cur: Any,
    events: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """
    Filter candidate events against recently inserted events.

    Queries the most recent event per symbol within the cooldown window
    and drops candidates suppressed by should_insert_event.
    """
    symbols = [e["symbol"] for e in events]
    cur.execute(
        """
        SELECT DISTINCT ON (symbol) symbol, timestamp, anomaly_score
        FROM anomaly_events
        WHERE symbol = ANY(%s)
          AND timestamp >= NOW() - (%s * INTERVAL '1 minute')
        ORDER BY symbol, timestamp DESC
        """,
        (symbols, ANOMALY_COOLDOWN_MINUTES),
    )
    last_by_symbol = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    kept: list[dict[str, Any]] = []
    for e in events:
        last_ts, last_score = last_by_symbol.get(e["symbol"], (None, None))
        insert, reason = should_insert_event(
            e["anomaly_score"], last_ts, last_score, now
        )
        if insert:
            kept.append(e)
        else:
            logger.info(
                "[DETECT] %s score=%.3f above threshold but suppressed: %s",
                e["symbol"],
                e["anomaly_score"],
                reason,
            )
    return kept


def insert_anomaly_events(
    cur: Any,
    events: list[dict[str, Any]],
) -> int:
    """
    Insert anomaly events into the anomaly_events table.

    Each event is annotated with macro-calendar context when its
    timestamp coincides with a scheduled release (annotation only —
    never suppression).

    Returns the number of rows inserted.
    """
    if not events:
        return 0

    from src.detection.macro_calendar import augment_description, macro_event_for

    rows = []
    for e in events:
        macro = macro_event_for(e["timestamp"])
        description = e["description"]
        if macro:
            description = augment_description(description, e["timestamp"])
        rows.append((
            e["timestamp"],
            e["symbol"],
            e["anomaly_score"],
            e["z_flag"],
            e["ewma_flag"],
            e["pca_flag"],
            description,
            macro["name"] if macro else None,
        ))

    sql = """
        INSERT INTO anomaly_events
            (timestamp, symbol, anomaly_score, z_flag, ewma_flag, pca_flag,
             description, macro_context)
        VALUES %s
    """
    execute_values(cur, sql, rows, page_size=50)
    try:
        from src.metrics import ANOMALIES_PERSISTED

        ANOMALIES_PERSISTED.inc(len(rows))
    except ImportError:
        pass
    return len(rows)


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def detect_z_score(
    features: list[dict[str, Any]],
    threshold: float = Z_SCORE_THRESHOLD,
) -> tuple[bool, float]:
    """
    Detector 1 — Z-Score Spike (weight 0.35).

    Checks the most recent z_score from the feature table.
    Flags if |z_score| > 2.5.
    Normalizes to [0, 1] via min(|z| / 4.0, 1.0).

    Returns (flag, raw_signal).
    """
    if not features:
        return False, 0.0

    z = features[-1].get("z_score")
    if z is None:
        return False, 0.0

    try:
        z_val = float(z)
    except (TypeError, ValueError):
        return False, 0.0

    flag = abs(z_val) > threshold
    raw_signal = min(abs(z_val) / Z_SCORE_NORMALIZER, 1.0)
    return flag, round(raw_signal, 6)


def detect_ewma_volatility(
    features: list[dict[str, Any]],
    multiplier: float = EWMA_MULTIPLIER,
) -> tuple[bool, float]:
    """
    Detector 2 — EWMA Volatility Spike (weight 0.35).

    Compares the most recent EWMA vol against the rolling mean of
    the last 100 rows. Flags if current > 2.0 × baseline.
    Normalizes via min(current / (baseline × 3.0), 1.0).

    Returns (flag, raw_signal).
    """
    if not features:
        return False, 0.0

    current_raw = features[-1].get("ewma_vol")
    if current_raw is None:
        return False, 0.0

    try:
        current = float(current_raw)
    except (TypeError, ValueError):
        return False, 0.0

    # Collect valid historical EWMA values (excluding the current row)
    historical = []
    for f in features[:-1]:
        val = f.get("ewma_vol")
        if val is not None:
            try:
                historical.append(float(val))
            except (TypeError, ValueError):
                continue

    if not historical:
        return False, 0.0

    baseline = float(np.mean(historical))

    if baseline <= 0:
        # Non-positive baseline means no meaningful comparison
        if current > 0:
            return True, min(current / 0.001, 1.0)  # Treat as extreme
        return False, 0.0

    flag = current > (multiplier * baseline)
    raw_signal = min(current / (baseline * EWMA_NORMALIZER), 1.0)
    return flag, round(raw_signal, 6)


def detect_pca_residual(
    features: list[dict[str, Any]],
    threshold: float = PCA_Z_THRESHOLD,
) -> tuple[bool, float]:
    """
    Detector 3 — PCA Residual (weight 0.30).

    Computes a rolling z-score of pca_residual over the last 100 rows.
    Flags if |z_pca| > 2.8.
    Normalizes via min(|z_pca| / 5.0, 1.0).

    Returns (flag, raw_signal).
    """
    if not features:
        return False, 0.0

    current_raw = features[-1].get("pca_residual")
    if current_raw is None:
        return False, 0.0

    try:
        current = float(current_raw)
    except (TypeError, ValueError):
        return False, 0.0

    # Collect valid historical PCA residual values (excluding current)
    historical = []
    for f in features[:-1]:
        val = f.get("pca_residual")
        if val is not None:
            try:
                historical.append(float(val))
            except (TypeError, ValueError):
                continue

    if len(historical) < 5:
        # Not enough history for a meaningful std
        return False, 0.0

    arr = np.array(historical, dtype=np.float64)
    rolling_mean = float(np.mean(arr))
    rolling_std = float(np.std(arr, ddof=1))

    if rolling_std <= 1e-12:
        # Zero variance — can't compute z-score
        if abs(current - rolling_mean) > 1e-6:
            # Any non-zero deviation from a flat history is notable
            return True, min(abs(current - rolling_mean) / 1.0, 1.0)
        return False, 0.0

    z_pca = (current - rolling_mean) / rolling_std
    flag = abs(z_pca) > threshold
    raw_signal = min(abs(z_pca) / PCA_NORMALIZER, 1.0)
    return flag, round(raw_signal, 6)


# ---------------------------------------------------------------------------
# Description Generator
# ---------------------------------------------------------------------------

def build_description(
    symbol: str,
    z_flag: bool,
    ewma_flag: bool,
    pca_flag: bool,
    z_score: Optional[float],
    z_signal: float,
    ewma_signal: float,
    pca_signal: float,
    pca_z_val: Optional[float],
    ewma_ratio: Optional[float],
    price: Optional[float],
) -> str:
    """
    Auto-generate a human-readable anomaly description.

    Composes clauses for each triggered detector, ordered by signal
    strength, then joins them into a coherent sentence.
    """
    clauses = []

    if z_flag and z_score is not None:
        direction = "spike" if z_score > 0 else "drop"
        clauses.append(
            f"Z-score {direction} (z={abs(z_score):.2f})"
        )

    if ewma_flag and ewma_ratio is not None:
        clauses.append(
            f"volatility surge ({ewma_ratio:.1f}x baseline)"
        )

    if pca_flag:
        z_pca_str = f", z_pca={pca_z_val:.2f}" if pca_z_val is not None else ""
        clauses.append(
            f"cross-asset factor breakdown{z_pca_str}"
        )

    if not clauses:
        # Below threshold but close — shouldn't happen since we only call
        # this for scored events, but defensive
        return (
            f"Elevated composite score on {symbol} "
            f"(z_sig={z_signal:.3f}, ewma_sig={ewma_signal:.3f}, pca_sig={pca_signal:.3f})"
        )

    # Build contextual suffix based on the primary trigger
    primary = max(
        [(z_signal, "z"), (ewma_signal, "ewma"), (pca_signal, "pca")],
        key=lambda x: x[0],
    )[1]

    context_map = {
        "z": "possible sudden price dislocation",
        "ewma": "possible regime shift or event-driven trading",
        "pca": "possible correlation structure breakdown",
    }
    context = context_map.get(primary, "requires investigation")

    price_str = f" at {price:.4f}" if price is not None else ""

    return f"{', '.join(clauses)} detected on {symbol}{price_str} — {context}"


# ---------------------------------------------------------------------------
# Core Detection Logic
# ---------------------------------------------------------------------------

def score_symbol(
    features: list[dict[str, Any]],
    regime: str = REGIME_MEDIUM,
) -> dict[str, Any]:
    """
    Run all three detectors on a symbol's feature window and compute
    the weighted composite anomaly score.

    `regime` scales each detector's threshold via REGIME_SCALE_FACTORS
    (wider thresholds in high-vol regimes). The regime is recorded in
    the result so stored anomalies are self-explanatory.
    """
    scale = scale_for_regime(regime)

    # Run detectors with regime-scaled thresholds
    z_flag, z_signal = detect_z_score(
        features, threshold=Z_SCORE_THRESHOLD * scale
    )
    ewma_flag, ewma_signal = detect_ewma_volatility(
        features, multiplier=EWMA_MULTIPLIER * scale
    )
    pca_flag, pca_signal = detect_pca_residual(
        features, threshold=PCA_Z_THRESHOLD * scale
    )

    # Composite score
    anomaly_score = round(
        W_Z_SCORE * z_signal
        + W_EWMA_VOL * ewma_signal
        + W_PCA_RESIDUAL * pca_signal,
        3,
    )

    # Extract raw values for description generation
    latest = features[-1] if features else {}
    z_score_raw = latest.get("z_score")
    pca_residual_raw = latest.get("pca_residual")

    # Compute EWMA ratio for description
    ewma_ratio = None
    if ewma_flag:
        current_ewma = latest.get("ewma_vol")
        historical_ewma = [
            float(f["ewma_vol"])
            for f in features[:-1]
            if f.get("ewma_vol") is not None
        ]
        if current_ewma and historical_ewma:
            baseline = np.mean(historical_ewma)
            if baseline > 0:
                ewma_ratio = float(current_ewma) / baseline

    # Compute PCA z-value for description
    pca_z_val = None
    if pca_flag and pca_residual_raw is not None:
        historical_pca = [
            float(f["pca_residual"])
            for f in features[:-1]
            if f.get("pca_residual") is not None
        ]
        if len(historical_pca) >= 5:
            arr = np.array(historical_pca)
            std = np.std(arr, ddof=1)
            if std > 1e-12:
                pca_z_val = (float(pca_residual_raw) - np.mean(arr)) / std

    # Build description
    description = build_description(
        symbol=latest.get("symbol", "UNKNOWN"),
        z_flag=z_flag,
        ewma_flag=ewma_flag,
        pca_flag=pca_flag,
        z_score=z_score_raw,
        z_signal=z_signal,
        ewma_signal=ewma_signal,
        pca_signal=pca_signal,
        pca_z_val=pca_z_val,
        ewma_ratio=ewma_ratio,
        price=latest.get("price"),
    )

    return {
        "symbol": latest.get("symbol", "UNKNOWN"),
        "timestamp": latest.get("timestamp"),
        "price": latest.get("price"),
        "z_score_raw": z_score_raw,
        "z_flag": z_flag,
        "z_signal": z_signal,
        "ewma_flag": ewma_flag,
        "ewma_signal": ewma_signal,
        "pca_flag": pca_flag,
        "pca_signal": pca_signal,
        "anomaly_score": anomaly_score,
        "description": description,
        "regime": regime,
    }


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def run_detection(db_conn: Any) -> list[dict[str, Any]]:
    """
    Run the full anomaly detection cycle across all 7 symbols.

    For each symbol:
        1. Fetch the last 100 rows from market_features
        2. Apply Z-Score, EWMA Vol, and PCA Residual detectors
        3. Compute the weighted composite score
        4. Insert an anomaly_events row if score > 0.30

    Args:
        db_conn: An open psycopg2 connection (caller manages lifecycle).

    Returns:
        List of result dicts (one per symbol) with scores, flags,
        and descriptions. Includes symbols below the insertion
        threshold for observability.
    """
    run_timestamp = datetime.now(timezone.utc)
    logger.info(
        "[DETECT] Starting detection cycle at %s across %d symbols",
        run_timestamp.isoformat(),
        len(SYMBOLS),
    )

    results: list[dict[str, Any]] = []
    events_to_insert: list[dict[str, Any]] = []

    cur = db_conn.cursor()

    try:
        # Pre-pass: fetch every symbol's feature window up front so the
        # lead-lag annotation sees returns across the full universe.
        features_by_symbol: dict[str, list[dict[str, Any]]] = {
            symbol: fetch_features(cur, symbol, window=FEATURE_WINDOW)
            for symbol in SYMBOLS
        }
        returns_by_symbol: dict[str, list[float]] = {
            symbol: [
                float(f["return_1m"])
                for f in features
                if f.get("return_1m") is not None
            ][-60:]
            for symbol, features in features_by_symbol.items()
        }

        for symbol in SYMBOLS:
            # Feature window (fetched in the pre-pass above)
            features = features_by_symbol[symbol]

            if not features:
                logger.warning(
                    "[DETECT] No features found for %s — skipping",
                    symbol,
                )
                results.append({
                    "symbol": symbol,
                    "timestamp": None,
                    "price": None,
                    "z_score_raw": None,
                    "z_flag": False,
                    "z_signal": 0.0,
                    "ewma_flag": False,
                    "ewma_signal": 0.0,
                    "pca_flag": False,
                    "pca_signal": 0.0,
                    "anomaly_score": 0.0,
                    "description": f"No feature data available for {symbol}",
                })
                continue

            logger.info(
                "[DETECT] %s: %d feature rows, latest timestamp=%s",
                symbol,
                len(features),
                features[-1].get("timestamp"),
            )

            # Volatility regime: current EWMA vol percentile within its
            # own trailing distribution (the fetched feature window).
            vol_series = [
                float(f["ewma_vol"])
                for f in features
                if f.get("ewma_vol") is not None
            ]
            current_vol = vol_series[-1] if vol_series else None
            regime = (
                classify_vol_percentile(current_vol, vol_series[:-1])
                if current_vol is not None
                else REGIME_MEDIUM
            )

            # Score (thresholds scaled by regime)
            result = score_symbol(features, regime=regime)
            results.append(result)

            # Log per-symbol result — regime is observable here by design
            flags_str = (
                f"Z={'ON' if result['z_flag'] else 'off'} "
                f"EWMA={'ON' if result['ewma_flag'] else 'off'} "
                f"PCA={'ON' if result['pca_flag'] else 'off'}"
            )
            logger.info(
                "[DETECT] %s score=%.3f regime=%s [%s] — %s",
                symbol,
                result["anomaly_score"],
                regime,
                flags_str,
                result["description"],
            )

            # Queue for insertion if above threshold
            if result["anomaly_score"] > SCORE_INSERT_THRESHOLD:
                description = result["description"] + f" [regime: {regime}]"

                # Lead-lag context: which paired asset likely moved
                # first (annotation only, same as macro context).
                lead_lag = lead_lag_for(symbol, returns_by_symbol)
                if lead_lag:
                    description = augment_description(description, lead_lag)

                events_to_insert.append({
                    "timestamp": result["timestamp"] or run_timestamp,
                    "symbol": result["symbol"],
                    "anomaly_score": result["anomaly_score"],
                    "z_flag": result["z_flag"],
                    "ewma_flag": result["ewma_flag"],
                    "pca_flag": result["pca_flag"],
                    "description": description,
                })

        # --- Suppress sustained anomalies (cooldown / escalation) ---
        if events_to_insert:
            events_to_insert = apply_cooldown(cur, events_to_insert, run_timestamp)

        # --- Insert qualifying events ---
        if events_to_insert:
            inserted = insert_anomaly_events(cur, events_to_insert)
            db_conn.commit()
            logger.info(
                "[DETECT] Inserted %d anomaly events (threshold > %.2f)",
                inserted,
                SCORE_INSERT_THRESHOLD,
            )
            for e in events_to_insert:
                logger.info(
                    "[EVENT] %s score=%.3f z=%s ewma=%s pca=%s",
                    e["symbol"],
                    e["anomaly_score"],
                    "Y" if e["z_flag"] else "N",
                    "Y" if e["ewma_flag"] else "N",
                    "Y" if e["pca_flag"] else "N",
                )
        else:
            logger.info(
                "[DETECT] No anomalies above threshold %.2f — nothing to insert",
                SCORE_INSERT_THRESHOLD,
            )

    except Exception as e:
        db_conn.rollback()
        logger.error(
            "[DETECT] Error during detection cycle: %s",
            e,
            exc_info=True,
        )
        raise
    finally:
        cur.close()

    # Summary
    scores = [r["anomaly_score"] for r in results]
    flagged = [r for r in results if r["anomaly_score"] > SCORE_INSERT_THRESHOLD]
    logger.info(
        "[DETECT] Cycle complete: %d symbols scored, %d flagged, "
        "score range [%.3f, %.3f], mean=%.3f",
        len(results),
        len(flagged),
        min(scores) if scores else 0.0,
        max(scores) if scores else 0.0,
        np.mean(scores) if scores else 0.0,
    )

    return results


# ---------------------------------------------------------------------------
# Standalone Runner
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Run the anomaly engine as a standalone script.

    Connects to PostgreSQL, runs one detection cycle, prints
    a formatted results table, and exits. Useful for testing,
    debugging, and manual execution outside of Airflow.
    """
    setup_logging()

    logger.info("=" * 72)
    logger.info("  Anomaly Scoring Engine — Standalone Run")
    logger.info("  DB: %s", DATABASE_URL.split("@")[-1])
    logger.info("  Symbols: %s", ", ".join(SYMBOLS))
    logger.info("  Threshold: > %.2f", SCORE_INSERT_THRESHOLD)
    logger.info("  Weights: Z=%.2f  EWMA=%.2f  PCA=%.2f",
                W_Z_SCORE, W_EWMA_VOL, W_PCA_RESIDUAL)
    logger.info("=" * 72)
    logger.info("")

    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        logger.info("[DB] Connected successfully")

        results = run_detection(conn)

        # --- Print formatted results table ---
        logger.info("")
        logger.info("=" * 72)
        logger.info("  RESULTS")
        logger.info("=" * 72)
        logger.info(
            "%-12s %8s %8s %8s %8s %8s %8s  %s",
            "SYMBOL", "SCORE", "Z_SIG", "EW_SIG", "PCA_SIG",
            "Z_FLG", "EW_FLG", "PCA_FLG",
        )
        logger.info("-" * 72)

        for r in results:
            logger.info(
                "%-12s %8.3f %8.4f %8.4f %8.4f %8s %8s %8s",
                r["symbol"],
                r["anomaly_score"],
                r["z_signal"],
                r["ewma_signal"],
                r["pca_signal"],
                "Y" if r["z_flag"] else ".",
                "Y" if r["ewma_flag"] else ".",
                "Y" if r["pca_flag"] else ".",
            )

        logger.info("-" * 72)

        # --- Print flagged anomalies with descriptions ---
        flagged = [r for r in results if r["anomaly_score"] > SCORE_INSERT_THRESHOLD]
        if flagged:
            logger.info("")
            logger.info("FLAGGED ANOMALIES (%d):", len(flagged))
            logger.info("-" * 72)
            for r in flagged:
                logger.info("  [%s] %.3f — %s", r["symbol"], r["anomaly_score"], r["description"])
        else:
            logger.info("")
            logger.info("No anomalies above threshold %.2f.", SCORE_INSERT_THRESHOLD)

        logger.info("")
        logger.info("=" * 72)

        # --- Return code for scripting ---
        if flagged:
            sys.exit(1)  # Exit 1 if anomalies found (useful in alerting scripts)
        else:
            sys.exit(0)

    except psycopg2.OperationalError as e:
        logger.error("[DB] Connection failed: %s", e)
        logger.error("[DB] Ensure PostgreSQL is running and DATABASE_URL is correct.")
        sys.exit(2)
    except Exception as e:
        logger.error("[FATAL] %s", e, exc_info=True)
        sys.exit(3)
    finally:
        if conn is not None:
            try:
                conn.close()
                logger.info("[DB] Connection closed")
            except Exception:
                pass


if __name__ == "__main__":
    main()