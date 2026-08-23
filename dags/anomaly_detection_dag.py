"""
Cross-Asset Anomaly Detection DAG.

Runs every 5 minutes to check for fresh market data, execute the
anomaly scoring engine against all 7 symbols, and evaluate whether
any high-risk alerts should be raised.

Airflow connection required:
    conn_id = "market_anomalies_db"
    conn_type = "Postgres"
    host, schema, login, password, port configured accordingly
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

# ---------------------------------------------------------------------------
# Add project root to path so we can import src.detection.anomaly_engine
# ---------------------------------------------------------------------------
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.detection.anomaly_engine import run_detection

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
    "execution_timeout": timedelta(minutes=4),
}

# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

dag = DAG(
    dag_id="cross_asset_anomaly_detection",
    default_args=DEFAULT_ARGS,
    description=(
        "Every 5 minutes: verify fresh data exists, run the anomaly scoring "
        "engine across 7 assets, and evaluate high-risk alerts."
    ),
    schedule="*/5 * * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["anomaly-detection", "market-data", "production"],
)


# ---------------------------------------------------------------------------
# Task 1: Check data freshness
# ---------------------------------------------------------------------------

def check_data_freshness(**context: Any) -> None:
    """
    Verify that the feature consumer has written new rows in the last
    5 minutes. If none are found, the Kafka producer pipeline may be
    down and an operator should investigate.
    """
    hook = PostgresHook(postgres_conn_id="market_anomalies_db")
    conn = hook.get_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT COUNT(*)
            FROM market_features
            WHERE timestamp >= NOW() - INTERVAL '5 minutes'
        """)
        count = cur.fetchone()[0]

        if count == 0:
            log.warning(
                "[FRESHNESS] No fresh data in the last 5 minutes — "
                "Kafka producer may be down or market is closed"
            )
        else:
            log.info(
                "[FRESHNESS] Found %d fresh rows in the last 5 minutes",
                count,
            )

        # Push count to XCom for downstream tasks that might want it
        context["ti"].xcom_push(key="fresh_row_count", value=count)

    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Task 2: Run anomaly detection
# ---------------------------------------------------------------------------

def run_anomaly_detection(**context: Any) -> None:
    """
    Execute the full anomaly scoring pipeline.

    Calls anomaly_engine.run_detection() which queries the last 100
    market_features rows per symbol, applies three weighted detectors
    (Z-Score, EWMA Volatility, PCA Residual), computes a composite
    score, and inserts qualifying events into anomaly_events.

    Results are logged per-symbol and high-risk symbols (score > 0.6)
    are pushed to XCom for the alert evaluation task.
    """
    hook = PostgresHook(postgres_conn_id="market_anomalies_db")

    # Get a raw psycopg2 connection (not SQLAlchemy) — this is what
    # run_detection expects.
    conn = hook.get_conn()
    conn.autocommit = False

    try:
        results = run_detection(db_conn=conn)

        high_risk_symbols: list[str] = []

        for r in results:
            symbol = r["symbol"]
            score = r["anomaly_score"]
            z_flag = r["z_flag"]
            ewma_flag = r["ewma_flag"]
            pca_flag = r["pca_flag"]

            flags = []
            if z_flag:
                flags.append("Z")
            if ewma_flag:
                flags.append("EWMA")
            if pca_flag:
                flags.append("PCA")
            flags_str = "|".join(flags) if flags else "none"

            log.info(
                "[%s] score=%.3f flags=[%s]",
                symbol,
                score,
                flags_str,
            )

            if score > 0.6:
                high_risk_symbols.append(f"{symbol} ({score:.3f})")

        # Push high-risk list to XCom
        context["ti"].xcom_push(
            key="high_risk_symbols",
            value=high_risk_symbols,
        )

        # Also push full results for the summary task
        summary = [
            {
                "symbol": r["symbol"],
                "score": r["anomaly_score"],
                "z_flag": r["z_flag"],
                "ewma_flag": r["ewma_flag"],
                "pca_flag": r["pca_flag"],
            }
            for r in results
        ]
        context["ti"].xcom_push(key="detection_summary", value=summary)

    except Exception:
        conn.rollback()
        log.error(
            "[DETECTION] Anomaly detection failed — rolling back transaction",
            exc_info=True,
        )
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Task 3: Evaluate alerts
# ---------------------------------------------------------------------------

def evaluate_alerts(**context: Any) -> None:
    """
    Pull detection results from XCom and evaluate whether high-risk
    alerts should be raised. Logs a structured alert summary suitable
    for log-based alerting systems (Datadog, CloudWatch, etc.).
    """
    ti = context["ti"]
    high_risk_symbols: list[str] = ti.xcom_pull(
        task_ids="run_anomaly_detection",
        key="high_risk_symbols",
    ) or []
    summary: list[dict] = ti.xcom_pull(
        task_ids="run_anomaly_detection",
        key="detection_summary",
    ) or []

    # --- Alert evaluation ---
    if high_risk_symbols:
        symbols_str = ", ".join(high_risk_symbols)
        log.warning(
            "ALERT: High anomaly scores detected: %s",
            symbols_str,
        )
    else:
        log.info("ALERT: No high-risk symbols in this cycle")

    # --- Full summary table ---
    log.info("─── Detection Cycle Summary ───")
    log.info("%-12s %8s %6s %6s %6s", "SYMBOL", "SCORE", "Z", "EWMA", "PCA")
    log.info("─" * 46)

    for r in summary:
        z = "Y" if r["z_flag"] else "."
        ew = "Y" if r["ewma_flag"] else "."
        pc = "Y" if r["pca_flag"] else "."
        log.info(
            "%-12s %8.3f %6s %6s %6s",
            r["symbol"],
            r["score"],
            z,
            ew,
            pc,
        )

    log.info("─" * 46)
    scores = [r["score"] for r in summary]
    if scores:
        log.info(
            "Range: [%.3f, %.3f]  Mean: %.3f  Flagged: %d/7",
            min(scores),
            max(scores),
            sum(scores) / len(scores),
            len(high_risk_symbols),
        )
    log.info("─── End Summary ───")

    # Push final alert state for potential downstream notification tasks
    ti.xcom_push(
        key="alert_triggered",
        value=len(high_risk_symbols) > 0,
    )


# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

t1 = PythonOperator(
    task_id="check_data_freshness",
    python_callable=check_data_freshness,
    dag=dag,
)

t2 = PythonOperator(
    task_id="run_anomaly_detection",
    python_callable=run_anomaly_detection,
    dag=dag,
)

t3 = PythonOperator(
    task_id="evaluate_alerts",
    python_callable=evaluate_alerts,
    dag=dag,
)

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

t1 >> t2 >> t3