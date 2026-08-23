"""
Market Data Cleanup DAG.

Runs daily at midnight to purge stale data from market_features
(older than 30 days) and correlation_snapshots (older than 7 days),
then logs a daily anomaly summary for operational visibility.

Retention rationale:
    - market_features (1-min bars, 7 assets): ~70K rows/day.
      30 days ≈ 2.1M rows — keeps the feature consumer's window
      queries fast while retaining enough history for backtesting.
    - correlation_snapshots (hourly, 21 pairs): ~504 rows/day.
      7 days ≈ 3.5K rows — correlations are only meaningful for
      recent regime analysis.

Airflow connection required:
    conn_id = "market_anomalies_db"
    conn_type = "Postgres"
    host, schema, login, password, port configured accordingly
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("airflow.task")

# ---------------------------------------------------------------------------
# Default arguments
# ---------------------------------------------------------------------------

DEFAULT_ARGS = {
    "owner": "likhith",
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
    "execution_timeout": timedelta(minutes=10),
}

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

dag = DAG(
    dag_id="market_data_cleanup",
    default_args=DEFAULT_ARGS,
    description=(
        "Daily midnight cleanup: purge stale market_features (>30d) "
        "and correlation_snapshots (>7d), then log daily anomaly summary."
    ),
    schedule="0 0 * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["cleanup", "maintenance", "market-data"],
)


# ---------------------------------------------------------------------------
# Task 1: Cleanup old market_features
# ---------------------------------------------------------------------------

def cleanup_old_features(**context: Any) -> None:
    """
    Delete market_features rows older than 30 days.

    Uses a single DELETE with a subquery to count affected rows
    before deletion, enabling accurate logging without a separate
    COUNT query.
    """
    hook = PostgresHook(postgres_conn_id="market_anomalies_db")
    conn = hook.get_conn()
    cur = conn.cursor()

    try:
        # Count rows that will be deleted
        cur.execute("""
            SELECT COUNT(*)
            FROM market_features
            WHERE timestamp < NOW() - INTERVAL '30 days'
        """)
        count_before = cur.fetchone()[0]

        if count_before == 0:
            log.info(
                "[CLEANUP] market_features: no rows older than 30 days — nothing to delete"
            )
            return

        # Delete
        cur.execute("""
            DELETE FROM market_features
            WHERE timestamp < NOW() - INTERVAL '30 days'
        """)
        deleted = cur.rowcount
        conn.commit()

        log.info(
            "[CLEANUP] market_features: deleted %d rows older than 30 days "
            "(expected %d, table size reduced accordingly)",
            deleted,
            count_before,
        )

        # Log current table size after cleanup
        cur.execute("SELECT COUNT(*) FROM market_features")
        remaining = cur.fetchone()[0]
        log.info(
            "[CLEANUP] market_features: %d rows remaining after cleanup",
            remaining,
        )

        context["ti"].xcom_push(key="features_deleted", value=deleted)
        context["ti"].xcom_push(key="features_remaining", value=remaining)

    except Exception:
        conn.rollback()
        log.error(
            "[CLEANUP] Failed to cleanup market_features",
            exc_info=True,
        )
        raise
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Task 2: Cleanup old correlation_snapshots
# ---------------------------------------------------------------------------

def cleanup_old_correlations(**context: Any) -> None:
    """
    Delete correlation_snapshots older than 7 days.

    Correlation data decays in relevance quickly — 7 days provides
    enough history for trend analysis while keeping the table small.
    """
    hook = PostgresHook(postgres_conn_id="market_anomalies_db")
    conn = hook.get_conn()
    cur = conn.cursor()

    try:
        # Count rows that will be deleted
        cur.execute("""
            SELECT COUNT(*)
            FROM correlation_snapshots
            WHERE timestamp < NOW() - INTERVAL '7 days'
        """)
        count_before = cur.fetchone()[0]

        if count_before == 0:
            log.info(
                "[CLEANUP] correlation_snapshots: no rows older than 7 days — nothing to delete"
            )
            return

        # Delete
        cur.execute("""
            DELETE FROM correlation_snapshots
            WHERE timestamp < NOW() - INTERVAL '7 days'
        """)
        deleted = cur.rowcount
        conn.commit()

        log.info(
            "[CLEANUP] correlation_snapshots: deleted %d rows older than 7 days "
            "(expected %d)",
            deleted,
            count_before,
        )

        # Log current table size
        cur.execute("SELECT COUNT(*) FROM correlation_snapshots")
        remaining = cur.fetchone()[0]
        log.info(
            "[CLEANUP] correlation_snapshots: %d rows remaining after cleanup",
            remaining,
        )

        context["ti"].xcom_push(key="correlations_deleted", value=deleted)
        context["ti"].xcom_push(key="correlations_remaining", value=remaining)

    except Exception:
        conn.rollback()
        log.error(
            "[CLEANUP] Failed to cleanup correlation_snapshots",
            exc_info=True,
        )
        raise
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Task 3: Daily anomaly summary
# ---------------------------------------------------------------------------

def daily_summary(**context: Any) -> None:
    """
    Query and log a summary of today's anomaly activity.

    Reports:
        - Total anomaly events inserted today
        - Highest anomaly score and which symbol
        - Most frequently flagged symbol (most events)
        - Breakdown by severity tier (high/medium/low)
        - Breakdown by detector flags (Z/EWMA/PCA)
    """
    hook = PostgresHook(postgres_conn_id="market_anomalies_db")
    conn = hook.get_conn()
    cur = conn.cursor()

    try:
        log.info("═══════════════════════════════════════════")
        log.info("  DAILY ANOMALY SUMMARY")
        log.info("═══════════════════════════════════════════")

        # --- Total events today ---
        cur.execute("""
            SELECT COUNT(*) AS total
            FROM anomaly_events
            WHERE timestamp >= DATE_TRUNC('day', NOW())
        """)
        total_today = cur.fetchone()[0]
        log.info("  Total anomaly events today: %d", total_today)

        if total_today == 0:
            log.info("  No anomalies detected today — system is quiet.")
            log.info("═══════════════════════════════════════════")
            return

        # --- Highest score today ---
        cur.execute("""
            SELECT symbol, anomaly_score, timestamp
            FROM anomaly_events
            WHERE timestamp >= DATE_TRUNC('day', NOW())
            ORDER BY anomaly_score DESC
            LIMIT 1
        """)
        top_row = cur.fetchone()
        if top_row:
            log.info(
                "  Highest score: %.3f on %s at %s",
                top_row[1],
                top_row[0],
                top_row[2],
            )

        # --- Most flagged symbol ---
        cur.execute("""
            SELECT symbol, COUNT(*) AS cnt
            FROM anomaly_events
            WHERE timestamp >= DATE_TRUNC('day', NOW())
            GROUP BY symbol
            ORDER BY cnt DESC
            LIMIT 1
        """)
        most_flagged = cur.fetchone()
        if most_flagged:
            log.info(
                "  Most flagged symbol: %s (%d events)",
                most_flagged[0],
                most_flagged[1],
            )

        # --- Severity breakdown ---
        log.info("  ─── Severity Breakdown ───")
        severity_ranges = [
            ("High   (≥ 0.6)", "anomaly_score >= 0.6"),
            ("Medium (0.3-0.6)", "anomaly_score >= 0.3 AND anomaly_score < 0.6"),
            ("Low    (< 0.3)", "anomaly_score < 0.3"),
        ]
        for label, condition in severity_ranges:
            cur.execute(f"""
                SELECT COUNT(*)
                FROM anomaly_events
                WHERE timestamp >= DATE_TRUNC('day', NOW())
                  AND {condition}
            """)
            count = cur.fetchone()[0]
            bar = "█" * min(count, 40)
            log.info("    %-18s %4d  %s", label, count, bar)

        # --- Detector flag breakdown ---
        log.info("  ─── Detector Flag Breakdown ───")
        for flag_name, flag_col in [
            ("Z-Score", "z_flag"),
            ("EWMA Vol", "ewma_flag"),
            ("PCA Residual", "pca_flag"),
        ]:
            cur.execute(f"""
                SELECT COUNT(*)
                FROM anomaly_events
                WHERE timestamp >= DATE_TRUNC('day', NOW())
                  AND {flag_col} = TRUE
            """)
            count = cur.fetchone()[0]
            pct = (count / total_today * 100) if total_today > 0 else 0
            log.info(
                "    %-14s %4d events (%5.1f%%)",
                flag_name,
                count,
                pct,
            )

        # --- Per-symbol breakdown ---
        log.info("  ─── Per-Symbol Breakdown ───")
        cur.execute("""
            SELECT
                symbol,
                COUNT(*) AS cnt,
                ROUND(MAX(anomaly_score)::numeric, 3) AS max_score,
                ROUND(AVG(anomaly_score)::numeric, 3) AS avg_score
            FROM anomaly_events
            WHERE timestamp >= DATE_TRUNC('day', NOW())
            GROUP BY symbol
            ORDER BY cnt DESC
        """)
        rows = cur.fetchall()
        log.info("    %-12s %6s %8s %8s", "SYMBOL", "COUNT", "MAX", "AVG")
        log.info("    %-12s %6s %8s %8s", "─" * 12, "─" * 6, "─" * 8, "─" * 8)
        for row in rows:
            log.info(
                "    %-12s %6d %8s %8s",
                row[0],
                row[1],
                row[2],
                row[3],
            )

        # --- Cleanup stats from upstream tasks ---
        features_deleted = context["ti"].xcom_pull(
            task_ids="cleanup_old_features",
            key="features_deleted",
        )
        features_remaining = context["ti"].xcom_pull(
            task_ids="cleanup_old_features",
            key="features_remaining",
        )
        correlations_deleted = context["ti"].xcom_pull(
            task_ids="cleanup_old_correlations",
            key="correlations_deleted",
        )
        correlations_remaining = context["ti"].xcom_pull(
            task_ids="cleanup_old_correlations",
            key="correlations_remaining",
        )

        log.info("  ─── Cleanup Stats ───")
        if features_deleted is not None:
            log.info(
                "    market_features:         deleted %s, remaining %s",
                features_deleted,
                features_remaining,
            )
        if correlations_deleted is not None:
            log.info(
                "    correlation_snapshots:  deleted %s, remaining %s",
                correlations_deleted,
                correlations_remaining,
            )

        log.info("═══════════════════════════════════════════")

    except Exception:
        log.error("[DAILY-SUMMARY] Failed to generate daily summary", exc_info=True)
        raise
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

t1 = PythonOperator(
    task_id="cleanup_old_features",
    python_callable=cleanup_old_features,
    dag=dag,
)

t2 = PythonOperator(
    task_id="cleanup_old_correlations",
    python_callable=cleanup_old_correlations,
    dag=dag,
)

t3 = PythonOperator(
    task_id="daily_summary",
    python_callable=daily_summary,
    dag=dag,
)

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

t1 >> t2 >> t3