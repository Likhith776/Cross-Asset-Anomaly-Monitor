#!/usr/bin/env python3
"""
Standalone scheduler for single-machine deployments.

Replaces Airflow for this project while running the exact same logic as
dags/anomaly_detection_dag.py and dags/cleanup_dag.py:

  Every 5 minutes (DETECTION_INTERVAL_SECONDS):
      1. Check data freshness (rows written by the feature consumer)
      2. Run the anomaly scoring engine across all 7 symbols
      3. Evaluate and log high-risk alerts

  Daily at 00:00 UTC:
      4. Purge market_features older than 30 days
      5. Purge correlation_snapshots older than 7 days
      6. Log the daily anomaly summary

The Airflow DAGs in dags/ remain valid and deployable when this system
moves to a real Airflow environment; this module is the lightweight
equivalent for a single-host `docker compose up` deployment.

Usage:
    python -m src.detection.scheduler
"""

import glob
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
from dotenv import load_dotenv

from src.detection.anomaly_engine import run_detection
from src.metrics import BATCH_CYCLE_SECONDS, start_metrics_server_if_configured

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/market_anomalies",
)

DETECTION_INTERVAL_SECONDS = 5 * 60        # 5 minutes, like the DAG
FEATURES_RETENTION_DAYS = 30
CORRELATIONS_RETENTION_DAYS = 7
DAILY_CLEANUP_HOUR_UTC = 0                 # 00:00 UTC, like the DAG
DB_CONNECT_BACKOFF_SECONDS = 10

# Live-database backups: pg_dump custom format (compressed) into BACKUP_DIR
# (a mounted volume in deployment). The demo database is regenerable from
# the seeder and is intentionally not backed up.
BACKUP_HOUR_UTC = 1                        # 01:00 UTC, after the cleanup cycle
BACKUP_RETENTION_FILES = 7
BACKUP_DIR = os.getenv("BACKUP_DIR", "/backups")

# Demo dataset auto-refresh: wipe + regenerate with fresh timestamps so the
# dashboard's DEMO mode always shows a current-looking 7-day window.
# Empty DEMO_DATABASE_URL disables the cycle entirely.
DEMO_DATABASE_URL = os.getenv("DEMO_DATABASE_URL", "").strip()
DEMO_REFRESH_SECONDS = int(os.getenv("DEMO_REFRESH_SECONDS", str(6 * 60 * 60)))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("scheduler")


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
        logger.info("[SHUTDOWN] %s received, finishing current cycle ...", name)

    @property
    def should_stop(self) -> bool:
        return self._requested


# ---------------------------------------------------------------------------
# Detection cycle (ported from anomaly_detection_dag.py)
# ---------------------------------------------------------------------------

def check_data_freshness(cur: Any) -> int:
    """
    Verify that the feature consumer has written new rows in the last
    5 minutes. If none are found, the Kafka producer pipeline may be
    down and an operator should investigate.
    """
    cur.execute("""
        SELECT COUNT(*)
        FROM market_features
        WHERE timestamp >= NOW() - INTERVAL '5 minutes'
    """)
    count = cur.fetchone()[0]

    if count == 0:
        logger.warning(
            "[FRESHNESS] No fresh data in the last 5 minutes — "
            "Kafka producer may be down or market is closed"
        )
    else:
        logger.info("[FRESHNESS] Found %d fresh rows in the last 5 minutes", count)

    return count


def evaluate_alerts(results: list[dict[str, Any]]) -> None:
    """
    Log high-risk alerts (score > 0.6) and a per-symbol summary table,
    mirroring the DAG's alert evaluation task.
    """
    high_risk_symbols = [
        f"{r['symbol']} ({r['anomaly_score']:.3f})"
        for r in results
        if r["anomaly_score"] > 0.6
    ]

    if high_risk_symbols:
        logger.warning(
            "ALERT: High anomaly scores detected: %s",
            ", ".join(high_risk_symbols),
        )
    else:
        logger.info("ALERT: No high-risk symbols in this cycle")

    logger.info("─── Detection Cycle Summary ───")
    logger.info("%-12s %8s %6s %6s %6s", "SYMBOL", "SCORE", "Z", "EW", "PCA")
    logger.info("─" * 46)
    for r in results:
        z = "Y" if r["z_flag"] else "."
        ew = "Y" if r["ewma_flag"] else "."
        pc = "Y" if r["pca_flag"] else "."
        logger.info(
            "%-12s %8.3f %6s %6s %6s",
            r["symbol"],
            r["anomaly_score"],
            z,
            ew,
            pc,
        )
    logger.info("─" * 46)

    scores = [r["anomaly_score"] for r in results]
    if scores:
        logger.info(
            "Range: [%.3f, %.3f]  Mean: %.3f  Flagged: %d/%d",
            min(scores),
            max(scores),
            sum(scores) / len(scores),
            len(high_risk_symbols),
            len(results),
        )
    logger.info("─── End Summary ───")


def run_detection_cycle(conn: Any) -> None:
    """One full detection cycle: freshness check, scoring, alert evaluation."""
    cur = conn.cursor()
    try:
        check_data_freshness(cur)
    finally:
        cur.close()
    # End the read transaction opened by the freshness check
    conn.rollback()

    try:
        results = run_detection(db_conn=conn)
        evaluate_alerts(results)
    except Exception:
        conn.rollback()
        logger.error("[DETECTION] Cycle failed — rolled back", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Cleanup cycle (ported from cleanup_dag.py)
# ---------------------------------------------------------------------------

def cleanup_table(
    cur: Any,
    table: str,
    retention_days: int,
) -> tuple[int, int]:
    """Delete rows older than the retention window. Returns (deleted, remaining)."""
    cur.execute(
        f"SELECT COUNT(*) FROM {table} WHERE timestamp < NOW() - INTERVAL '{retention_days} days'"
    )
    count_before = cur.fetchone()[0]

    if count_before == 0:
        logger.info("[CLEANUP] %s: no rows older than %d days — nothing to delete", table, retention_days)
    else:
        cur.execute(
            f"DELETE FROM {table} WHERE timestamp < NOW() - INTERVAL '{retention_days} days'"
        )
        deleted = cur.rowcount
        logger.info(
            "[CLEANUP] %s: deleted %d rows older than %d days (expected %d)",
            table,
            deleted,
            retention_days,
            count_before,
        )

    cur.execute(f"SELECT COUNT(*) FROM {table}")
    remaining = cur.fetchone()[0]
    logger.info("[CLEANUP] %s: %d rows remaining", table, remaining)
    return count_before, remaining


def daily_summary(cur: Any, features_stats: tuple[int, int], corr_stats: tuple[int, int]) -> None:
    """Log the daily anomaly activity summary (mirrors the DAG task)."""
    logger.info("═══════════════════════════════════════════")
    logger.info("  DAILY ANOMALY SUMMARY")
    logger.info("═══════════════════════════════════════════")

    cur.execute("""
        SELECT COUNT(*) FROM anomaly_events
        WHERE timestamp >= DATE_TRUNC('day', NOW())
    """)
    total_today = cur.fetchone()[0]
    logger.info("  Total anomaly events today: %d", total_today)

    if total_today == 0:
        logger.info("  No anomalies detected today — system is quiet.")
    else:
        cur.execute("""
            SELECT symbol, anomaly_score, timestamp FROM anomaly_events
            WHERE timestamp >= DATE_TRUNC('day', NOW())
            ORDER BY anomaly_score DESC LIMIT 1
        """)
        top = cur.fetchone()
        if top:
            logger.info("  Highest score: %.3f on %s at %s", top[1], top[0], top[2])

        cur.execute("""
            SELECT symbol, COUNT(*) AS cnt FROM anomaly_events
            WHERE timestamp >= DATE_TRUNC('day', NOW())
            GROUP BY symbol ORDER BY cnt DESC LIMIT 1
        """)
        most = cur.fetchone()
        if most:
            logger.info("  Most flagged symbol: %s (%d events)", most[0], most[1])

        logger.info("  ─── Severity Breakdown ───")
        for label, condition in [
            ("High   (>= 0.6)", "anomaly_score >= 0.6"),
            ("Medium (0.3-0.6)", "anomaly_score >= 0.3 AND anomaly_score < 0.6"),
            ("Low    (< 0.3)", "anomaly_score < 0.3"),
        ]:
            cur.execute(f"""
                SELECT COUNT(*) FROM anomaly_events
                WHERE timestamp >= DATE_TRUNC('day', NOW()) AND {condition}
            """)
            count = cur.fetchone()[0]
            logger.info("    %-18s %4d  %s", label, count, "█" * min(count, 40))

        logger.info("  ─── Detector Flag Breakdown ───")
        for flag_name, flag_col in [
            ("Z-Score", "z_flag"),
            ("EWMA Vol", "ewma_flag"),
            ("PCA Residual", "pca_flag"),
        ]:
            cur.execute(f"""
                SELECT COUNT(*) FROM anomaly_events
                WHERE timestamp >= DATE_TRUNC('day', NOW()) AND {flag_col} = TRUE
            """)
            count = cur.fetchone()[0]
            pct = (count / total_today * 100) if total_today > 0 else 0
            logger.info("    %-14s %4d events (%5.1f%%)", flag_name, count, pct)

        logger.info("  ─── Per-Symbol Breakdown ───")
        cur.execute("""
            SELECT symbol, COUNT(*) AS cnt,
                   ROUND(MAX(anomaly_score)::numeric, 3) AS max_score,
                   ROUND(AVG(anomaly_score)::numeric, 3) AS avg_score
            FROM anomaly_events
            WHERE timestamp >= DATE_TRUNC('day', NOW())
            GROUP BY symbol ORDER BY cnt DESC
        """)
        logger.info("    %-12s %6s %8s %8s", "SYMBOL", "COUNT", "MAX", "AVG")
        for row in cur.fetchall():
            logger.info("    %-12s %6d %8s %8s", row[0], row[1], row[2], row[3])

    logger.info("  ─── Cleanup Stats ───")
    logger.info(
        "    market_features:        deleted %d, remaining %d", *features_stats
    )
    logger.info(
        "    correlation_snapshots:  deleted %d, remaining %d", *corr_stats
    )
    logger.info("═══════════════════════════════════════════")


def run_cleanup_cycle(conn: Any) -> None:
    """One full cleanup cycle: retention purge + daily summary."""
    conn.rollback()  # clear any transaction state on the pooled connection
    cur = conn.cursor()
    try:
        features_stats = cleanup_table(cur, "market_features", FEATURES_RETENTION_DAYS)
        corr_stats = cleanup_table(cur, "correlation_snapshots", CORRELATIONS_RETENTION_DAYS)
        conn.commit()
        daily_summary(cur, features_stats, corr_stats)
    except Exception:
        conn.rollback()
        logger.error("[CLEANUP] Cycle failed — rolled back", exc_info=True)
        raise
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Demo dataset refresh (replaces the old demo-refresher service)
# ---------------------------------------------------------------------------

def _run_script(script: str, env_overrides: dict) -> int:
    """Run a project script as a subprocess with overridden env. Returns exit code."""
    env = dict(os.environ, **env_overrides)
    return subprocess.run(
        [sys.executable, script], env=env, check=False
    ).returncode


def ensure_demo_schema() -> None:
    """Create demo tables if missing (idempotent)."""
    if not DEMO_DATABASE_URL:
        return
    code = _run_script("scripts/init_db.py", {"DATABASE_URL": DEMO_DATABASE_URL})
    if code != 0:
        logger.error("[DEMO] init_db for the demo dataset failed (exit %d)", code)


def run_demo_refresh() -> bool:
    """Wipe + regenerate the demo dataset with fresh timestamps."""
    if not DEMO_DATABASE_URL:
        return False
    logger.info("[DEMO] Refreshing demo dataset (regenerates ~74k rows) ...")
    code = _run_script(
        "scripts/seed_demo_data.py",
        {"DATABASE_URL": DEMO_DATABASE_URL, "DROP_EXISTING": "1"},
    )
    if code == 0:
        logger.info("[DEMO] Demo dataset refreshed — next refresh in %ds", DEMO_REFRESH_SECONDS)
        return True
    logger.error("[DEMO] Demo refresh failed (exit %d) — retrying next cycle", code)
    return False


# ---------------------------------------------------------------------------
# Live database backup
# ---------------------------------------------------------------------------

def run_backup() -> bool:
    """
    Dump the live database in pg_dump custom format (natively compressed,
    restorable via pg_restore), pruning old backups beyond retention.
    """
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
    except OSError as e:
        logger.error("[BACKUP] Cannot create %s: %s", BACKUP_DIR, e)
        return False

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUP_DIR, f"market_anomalies_{stamp}.dump")

    logger.info("[BACKUP] Dumping live database to %s ...", path)
    try:
        result = subprocess.run(
            [
                "pg_dump", "--format=custom", "--no-owner", "--no-privileges",
                "--file", path, DATABASE_URL,
            ],
            check=False,
        )
    except FileNotFoundError:
        logger.error("[BACKUP] pg_dump not found — install postgresql-client in the image")
        return False

    if result.returncode != 0:
        logger.error("[BACKUP] pg_dump failed (exit %d)", result.returncode)
        if os.path.exists(path):
            os.remove(path)
        return False

    # Retention: keep the newest N dumps
    dumps = sorted(glob.glob(os.path.join(BACKUP_DIR, "market_anomalies_*.dump")))
    for old in dumps[:-BACKUP_RETENTION_FILES] if len(dumps) > BACKUP_RETENTION_FILES else []:
        os.remove(old)
        logger.info("[BACKUP] Pruned %s", os.path.basename(old))

    logger.info(
        "[BACKUP] Saved %s (%d KiB) — retention keeps %d",
        os.path.basename(path),
        os.path.getsize(path) // 1024,
        BACKUP_RETENTION_FILES,
    )
    return True


def restore_instructions() -> None:
    """Log the restore command so it's discoverable in the container logs."""
    logger.info(
        "[BACKUP] Restore with: pg_restore --clean --if-exists --no-owner "
        "-d \"$DATABASE_URL\" /backups/<file>.dump"
    )


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

def next_daily_time(now: datetime, hour: int) -> datetime:
    """Next UTC boundary of the given hour after `now`."""
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def get_connection():
    """Open a database connection, retrying with backoff until shutdown."""
    while True:
        try:
            return psycopg2.connect(DATABASE_URL)
        except psycopg2.OperationalError as e:
            logger.error("[DB] Connection failed: %s — retrying in %ds", e, DB_CONNECT_BACKOFF_SECONDS)
            time.sleep(DB_CONNECT_BACKOFF_SECONDS)


def main() -> None:
    setup_logging()
    shutdown = ShutdownHandler()
    start_metrics_server_if_configured()

    logger.info("=" * 64)
    logger.info("  Detection Scheduler — Starting")
    logger.info("  DB:       %s", DATABASE_URL.split("@")[-1])
    logger.info("  Detect:   every %d seconds", DETECTION_INTERVAL_SECONDS)
    logger.info("  Cleanup:  daily at %02d:00 UTC (features %dd, correlations %dd)",
                DAILY_CLEANUP_HOUR_UTC, FEATURES_RETENTION_DAYS, CORRELATIONS_RETENTION_DAYS)
    if DEMO_DATABASE_URL:
        logger.info("  Demo:     refresh every %ds (%s)",
                    DEMO_REFRESH_SECONDS, DEMO_DATABASE_URL.split("@")[-1])
    else:
        logger.info("  Demo:     disabled (DEMO_DATABASE_URL not set)")
    logger.info("  Backup:   daily at %02d:00 UTC -> %s (keep %d)",
                BACKUP_HOUR_UTC, BACKUP_DIR, BACKUP_RETENTION_FILES)
    logger.info("=" * 64)

    ensure_demo_schema()

    next_detection = datetime.now(timezone.utc)
    next_cleanup = next_daily_time(datetime.now(timezone.utc), DAILY_CLEANUP_HOUR_UTC)
    next_backup = datetime.now(timezone.utc)               # verify at startup
    next_demo_refresh = datetime.now(timezone.utc)         # seed once at startup

    # Run one cycle immediately on startup so the system is warm
    conn = get_connection()
    try:
        while not shutdown.should_stop:
            now = datetime.now(timezone.utc)

            if DEMO_DATABASE_URL and now >= next_demo_refresh:
                try:
                    run_demo_refresh()
                except Exception:
                    logger.error("[DEMO] Refresh cycle crashed", exc_info=True)
                next_demo_refresh = datetime.now(timezone.utc) + timedelta(seconds=DEMO_REFRESH_SECONDS)
                continue

            if now >= next_backup:
                try:
                    if run_backup():
                        restore_instructions()
                except Exception:
                    logger.error("[BACKUP] Cycle crashed", exc_info=True)
                next_backup = next_daily_time(datetime.now(timezone.utc), BACKUP_HOUR_UTC)
                continue

            if now >= next_cleanup:
                logger.info("[SCHED] Running daily cleanup cycle")
                try:
                    run_cleanup_cycle(conn)
                except Exception:
                    conn.rollback()
                next_cleanup = next_daily_time(datetime.now(timezone.utc), DAILY_CLEANUP_HOUR_UTC)
                continue

            if now >= next_detection:
                try:
                    with BATCH_CYCLE_SECONDS.time():
                        run_detection_cycle(conn)
                except Exception:
                    logger.warning("[SCHED] Detection cycle failed — will retry next interval")
                next_detection = datetime.now(timezone.utc) + timedelta(seconds=DETECTION_INTERVAL_SECONDS)

            time.sleep(1)
    finally:
        conn.close()
        logger.info("[SHUTDOWN] Scheduler stopped cleanly")


if __name__ == "__main__":
    main()
