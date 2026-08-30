"""
FastAPI application for the Cross-Asset Anomaly Detection System.

Provides async endpoints for asset status, anomaly events, correlation
matrices, and time-series chart data. All endpoints read from
PostgreSQL via async SQLAlchemy + asyncpg.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.database import (
    AnomalyEvent,
    AnomalyFeedback,
    CorrelationSnapshot,
    MarketFeature,
    async_session_factory,
    demo_session_factory,
)
from src.api.models import (
    AnomalyEventResponse,
    AnomalyFeedbackRequest,
    AnomalyFeedbackResponse,
    AnomalyPrecisionResponse,
    AssetStatus,
    ChartPoint,
    CorrelationMatrix,
    DetectorPrecision,
    HealthResponse,
    MetaResponse,
)
from src.detection.lead_lag import extract_lead_lag
from src.precision import compute_precision, detector_from_description

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Universe (symbols + labels) comes from config/universe.json — single
# source of truth shared by every entry point.
from src.universe import load_universe  # noqa: E402

_UNIVERSE = load_universe()
SYMBOLS: list[str] = list(_UNIVERSE.symbols)
SYMBOL_LABELS: dict[str, str] = dict(_UNIVERSE.labels)

# Detector weights — must match anomaly_engine.py exactly
W_Z_SCORE = 0.35
W_EWMA_VOL = 0.35
W_PCA_RESIDUAL = 0.30

# Detector normalizers — must match anomaly_engine.py exactly
Z_SCORE_NORMALIZER = 4.0
EWMA_NORMALIZER = 3.0
PCA_NORMALIZER = 5.0

# Risk level thresholds
RISK_LOW_MAX = 0.3
RISK_MEDIUM_MAX = 0.6

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("api")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Cross-Asset Anomaly Detection API",
    description="Real-time anomaly monitoring across equities, crypto, commodities, FX, and rates",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus exposition: always mounted (inert without prometheus_client).
from src.metrics import DB_QUERY_SECONDS, mount_metrics  # noqa: E402

mount_metrics(app)


# ---------------------------------------------------------------------------
# Dataset routing (live pipeline data vs seeded demo data)
# ---------------------------------------------------------------------------

async def get_session(
    dataset: str = Query("live", pattern="^(live|demo)$"),
) -> AsyncSession:
    """
    FastAPI dependency that yields an async database session.

    Every endpoint accepts ?dataset=live|demo (default live). Live reads
    the pipeline's primary database; demo reads the separate seeded
    demo database (DEMO_DATABASE_URL) so the two never blend.
    """
    if dataset == "demo" and demo_session_factory is None:
        raise HTTPException(
            status_code=503,
            detail="Demo dataset not configured — set DEMO_DATABASE_URL and seed it "
                   "(see README: Demo dataset).",
        )
    factory = demo_session_factory if dataset == "demo" else async_session_factory
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Score Computation (mirrors anomaly_engine.py exactly)
# ---------------------------------------------------------------------------

def compute_composite_score(
    z_score: Optional[float],
    ewma_vol: Optional[float],
    pca_residual: Optional[float],
    ewma_history: Optional[list[float]] = None,
    pca_history: Optional[list[float]] = None,
) -> float:
    """
    Compute the weighted composite anomaly score.

    Mirrors the three detectors in anomaly_engine.py exactly (including
    edge cases) so API-computed scores match the events the engine stores.
    """
    # Cast to float to handle Postgres DECIMAL types
    z_score = float(z_score) if z_score is not None else None
    ewma_vol = float(ewma_vol) if ewma_vol is not None else None
    pca_residual = float(pca_residual) if pca_residual is not None else None

    # Z-score signal
    z_signal = 0.0
    if z_score is not None:
        z_signal = min(abs(z_score) / Z_SCORE_NORMALIZER, 1.0)

    # EWMA volatility signal
    ewma_signal = 0.0
    if ewma_vol is not None and ewma_history:
        baseline = float(np.mean([float(v) for v in ewma_history]))
        if baseline <= 0:
            # Non-positive baseline: engine treats any positive vol as extreme
            if ewma_vol > 0:
                ewma_signal = min(ewma_vol / 0.001, 1.0)
        else:
            ewma_signal = min(ewma_vol / (baseline * EWMA_NORMALIZER), 1.0)

    # PCA residual signal
    pca_signal = 0.0
    if pca_residual is not None and pca_history and len(pca_history) >= 5:
        arr = np.array([float(v) for v in pca_history], dtype=np.float64)
        rolling_mean = float(np.mean(arr))
        rolling_std = float(np.std(arr, ddof=1))
        if rolling_std <= 1e-12:
            # Zero variance: any non-zero deviation from flat history counts
            if abs(pca_residual - rolling_mean) > 1e-6:
                pca_signal = min(abs(pca_residual - rolling_mean) / 1.0, 1.0)
        else:
            z_pca = (pca_residual - rolling_mean) / rolling_std
            pca_signal = min(abs(z_pca) / PCA_NORMALIZER, 1.0)

    # Match the engine: round each signal before weighting
    z_signal = round(z_signal, 6)
    ewma_signal = round(ewma_signal, 6)
    pca_signal = round(pca_signal, 6)

    return round(W_Z_SCORE * z_signal + W_EWMA_VOL * ewma_signal + W_PCA_RESIDUAL * pca_signal, 3)


def classify_risk(score: float) -> str:
    """Map composite score to risk level string."""
    if score >= RISK_MEDIUM_MAX:
        return "high"
    if score >= RISK_LOW_MAX:
        return "medium"
    return "low"


def downsample_points(points: list[ChartPoint], max_points: int = 1440) -> list[ChartPoint]:
    """
    Evenly thin a point list to at most max_points, always keeping the
    latest point. Scores are computed on the dense series before calling
    this so rolling windows stay accurate.
    """
    if len(points) <= max_points:
        return points
    stride = -(-len(points) // max_points)  # ceil division
    sampled = points[::stride]
    if sampled[-1] is not points[-1]:
        sampled.append(points[-1])
    return sampled


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)):
    """
    Health check that verifies database connectivity on every call.
    """
    now = datetime.now(timezone.utc)
    try:
        await session.execute(text("SELECT 1"))
        return HealthResponse(status="ok", timestamp=now, db="connected")
    except Exception as e:
        logger.error("[HEALTH] Database check failed: %s", e)
        return HealthResponse(status="error", timestamp=now, db=f"error: {e}")


@app.get("/assets", response_model=list[AssetStatus])
async def get_assets(session: AsyncSession = Depends(get_session)):
    """
    Latest status for all 7 tracked assets.

    Fetches the last 100 feature rows per symbol to compute the composite
    anomaly score using the same weighted detector logic as the anomaly
    engine. Returns the most recent data point per symbol with the
    computed score and risk classification.
    """
    # Single query: last 100 rows per symbol using ROW_NUMBER
    stmt = text("""
        SELECT * FROM (
            SELECT
                timestamp, symbol, price, return_1m, return_5m,
                z_score, ewma_vol, pca_residual, volume,
                ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp DESC) AS rn
            FROM market_features
            WHERE symbol = ANY(:symbols)
        ) sub
        WHERE rn <= 100
        ORDER BY symbol, timestamp ASC
    """)
    with DB_QUERY_SECONDS.labels("/assets").time():
        result = await session.execute(stmt, {"symbols": SYMBOLS})
    rows = result.mappings().all()

    # Group by symbol, preserving time order (oldest first)
    by_symbol: dict[str, list[dict]] = {s: [] for s in SYMBOLS}
    for row in rows:
        sym = row["symbol"]
        if sym in by_symbol:
            by_symbol[sym].append(dict(row))

    # Build response for each symbol
    response: list[AssetStatus] = []
    for symbol in SYMBOLS:
        records = by_symbol[symbol]

        if not records:
            response.append(AssetStatus(
                symbol=symbol,
                regime="unknown",
                composite_score=0.0,
                risk_level="low",
            ))
            continue

        latest = records[-1]

        # Extract rolling histories (exclude the latest row)
        ewma_history = [
            r["ewma_vol"] for r in records[:-1]
            if r.get("ewma_vol") is not None
        ]
        pca_history = [
            r["pca_residual"] for r in records[:-1]
            if r.get("pca_residual") is not None
        ]

        # Volatility regime: current EWMA vol percentile within its own
        # trailing distribution (same classifier the detectors scale by).
        from src.detection.regime import classify_vol_percentile

        latest_ewma = latest.get("ewma_vol")
        regime = (
            classify_vol_percentile(
                float(latest_ewma), [float(v) for v in ewma_history]
            )
            if latest_ewma is not None
            else "unknown"
        )

        # Compute composite score
        score = compute_composite_score(
            z_score=latest.get("z_score"),
            ewma_vol=latest.get("ewma_vol"),
            pca_residual=latest.get("pca_residual"),
            ewma_history=ewma_history,
            pca_history=pca_history,
        )

        response.append(AssetStatus(
            symbol=symbol,
            label=SYMBOL_LABELS.get(symbol),
            price=latest.get("price"),
            regime=regime,
            z_score=latest.get("z_score"),
            ewma_vol=latest.get("ewma_vol"),
            pca_residual=latest.get("pca_residual"),
            return_1m=latest.get("return_1m"),
            timestamp=latest.get("timestamp"),
            composite_score=score,
            risk_level=classify_risk(score),
        ))

    return response


@app.get("/anomalies", response_model=list[AnomalyEventResponse])
async def get_anomalies(
    limit: int = Query(50, ge=1, le=200),
    min_score: float = Query(0.0, ge=0.0, le=1.0),
    session: AsyncSession = Depends(get_session),
):
    """
    Latest anomaly events across all symbols.

    Results are ordered by timestamp descending. Limit is capped at 200.
    """
    # Cap limit at 200 as specified
    effective_limit = min(limit, 200)

    stmt = text("""
        SELECT id, timestamp, symbol, anomaly_score,
               z_flag, ewma_flag, pca_flag, description, macro_context,
               llm_explanation
        FROM anomaly_events
        WHERE anomaly_score >= :min_score
        ORDER BY timestamp DESC
        LIMIT :limit
    """)
    with DB_QUERY_SECONDS.labels("/anomalies").time():
        result = await session.execute(stmt, {
            "min_score": min_score,
            "limit": effective_limit,
        })
    rows = result.mappings().all()

    return [
        AnomalyEventResponse(
            id=r["id"],
            timestamp=r["timestamp"],
            symbol=r["symbol"],
            anomaly_score=float(r["anomaly_score"]),
            z_flag=bool(r["z_flag"]),
            ewma_flag=bool(r["ewma_flag"]),
            pca_flag=bool(r["pca_flag"]),
            description=r["description"],
            macro_context=r["macro_context"],
            llm_explanation=r["llm_explanation"],
            lead_lag=extract_lead_lag(r["description"]),
        )
        for r in rows
    ]


@app.get("/anomalies/{symbol}", response_model=list[AnomalyEventResponse])
async def get_anomalies_by_symbol(
    symbol: str,
    days: int = Query(7, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    """
    Anomaly history for a single symbol over the last N days.
    """
    if symbol not in SYMBOLS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown symbol: {symbol}. Valid: {', '.join(SYMBOLS)}",
        )

    # Build the interval string safely (parameterized intervals not supported by asyncpg)
    interval_str = f"{days} days"
    stmt = text(f"""
        SELECT id, timestamp, symbol, anomaly_score,
               z_flag, ewma_flag, pca_flag, description, macro_context,
               llm_explanation
        FROM anomaly_events
        WHERE symbol = :symbol
          AND timestamp >= NOW() - INTERVAL '{interval_str}'
        ORDER BY timestamp DESC
    """)
    with DB_QUERY_SECONDS.labels("/anomalies/{symbol}").time():
        result = await session.execute(stmt, {"symbol": symbol})
    rows = result.mappings().all()

    return [
        AnomalyEventResponse(
            id=r["id"],
            timestamp=r["timestamp"],
            symbol=r["symbol"],
            anomaly_score=float(r["anomaly_score"]),
            z_flag=bool(r["z_flag"]),
            ewma_flag=bool(r["ewma_flag"]),
            pca_flag=bool(r["pca_flag"]),
            description=r["description"],
            macro_context=r["macro_context"],
            llm_explanation=r["llm_explanation"],
            lead_lag=extract_lead_lag(r["description"]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Anomaly feedback + precision
# ---------------------------------------------------------------------------

# Detector attribution lives in src/precision.py (single source of
# truth, shared with the livesite publisher and the report generator).


@app.post(
    "/anomalies/{anomaly_id}/feedback",
    response_model=AnomalyFeedbackResponse,
)
async def post_anomaly_feedback(
    anomaly_id: int,
    payload: AnomalyFeedbackRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Record a human judgment (confirmed / false_positive) on an
    anomaly_event. The same event can be labeled many times — the
    rolling-precision endpoint uses the *most recent* label per event.
    """
    stmt = text("SELECT 1 FROM anomaly_events WHERE id = :id")
    exists = await session.execute(stmt, {"id": anomaly_id})
    if exists.first() is None:
        raise HTTPException(
            status_code=404, detail=f"anomaly_event {anomaly_id} not found"
        )

    insert = text("""
        INSERT INTO anomaly_feedback (anomaly_event_id, label, noted_at, note)
        VALUES (:event_id, :label, NOW(), :note)
        RETURNING id, anomaly_event_id, label, noted_at, note
    """)
    row = (
        await session.execute(
            insert,
            {
                "event_id": anomaly_id,
                "label": payload.label,
                "note": payload.note,
            },
        )
    ).mappings().one()
    await session.commit()

    return AnomalyFeedbackResponse(
        id=row["id"],
        anomaly_event_id=row["anomaly_event_id"],
        label=row["label"],
        noted_at=row["noted_at"],
        note=row["note"],
    )


@app.get("/anomaly-precision", response_model=AnomalyPrecisionResponse)
async def get_anomaly_precision(
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    """
    Rolling-window precision: for every anomaly in the last `days`
    days that has at least one feedback row, take the most recent
    label. confirmed / (confirmed + false_positive) is precision; the
    `by_detector` breakdown is computed by joining back to the
    description string on the underlying anomaly_events row.
    """
    interval = f"{days} days"

    # Most recent label per event, filtered to the rolling window
    latest_per_event = text(f"""
        WITH latest AS (
            SELECT DISTINCT ON (anomaly_event_id)
                anomaly_event_id, label, noted_at
            FROM anomaly_feedback
            ORDER BY anomaly_event_id, noted_at DESC
        )
        SELECT
            ae.id            AS event_id,
            ae.description   AS description,
            latest.label     AS label,
            latest.noted_at  AS noted_at
        FROM latest
        JOIN anomaly_events ae ON ae.id = latest.anomaly_event_id
        WHERE latest.noted_at >= NOW() - INTERVAL '{interval}'
    """)

    rows = (await session.execute(latest_per_event)).mappings().all()

    # Hand the joined rows to the same pure function the livesite
    # publisher uses, so both surfaces return identical numbers.
    result = compute_precision(
        (
            {
                "anomaly_event_id": r["event_id"],
                "label": r["label"],
                "noted_at": r["noted_at"],
                "description": r["description"],
            }
            for r in rows
        ),
        window_days=days,
    )
    return AnomalyPrecisionResponse(**result)


@app.get("/meta", response_model=MetaResponse)
async def get_meta(
    dataset: str = Query("live", pattern="^(live|demo)$"),
    session: AsyncSession = Depends(get_session),
):
    """
    Dataset metadata for dashboard rendering.

    demo_data_end is detected automatically: seeded rows are backdated
    (their timestamp predates their created_at by more than 5 minutes),
    while live rows are written within seconds of their timestamp. The
    boundary is the newest backdated timestamp, i.e. where demo data
    ends and live data begins. Always null for the demo dataset itself
    (the whole dataset is demo — the dashboard badges it instead).
    """
    result = await session.execute(text("""
        SELECT MAX(timestamp)
        FROM market_features
        WHERE timestamp <= created_at - INTERVAL '5 minutes'
    """))
    demo_end = result.scalar()
    return MetaResponse(
        server_time=datetime.now(timezone.utc),
        demo_data_end=demo_end if dataset == "live" else None,
    )


@app.get("/correlations", response_model=CorrelationMatrix)
async def get_correlations(session: AsyncSession = Depends(get_session)):
    """
    Latest correlation snapshot as a 7×7 symmetric matrix.

    Queries the most recent correlation value for each unique
    (symbol_a, symbol_b) pair and assembles them into a matrix
    indexed by the standard symbol ordering.
    """
    stmt = text("""
        SELECT DISTINCT ON (symbol_a, symbol_b)
            symbol_a, symbol_b, correlation
        FROM correlation_snapshots
        ORDER BY symbol_a, symbol_b, timestamp DESC
    """)
    result = await session.execute(stmt)
    rows = result.mappings().all()

    # Build index map
    symbol_index = {s: i for i, s in enumerate(SYMBOLS)}
    n = len(SYMBOLS)

    # Initialize identity matrix (diagonal = 1.0)
    matrix = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]

    # Fill in off-diagonal entries (matrix is symmetric)
    missing_pairs = []
    for row in rows:
        sym_a = row["symbol_a"]
        sym_b = row["symbol_b"]
        corr = row["correlation"]

        if sym_a not in symbol_index or sym_b not in symbol_index:
            continue

        i = symbol_index[sym_a]
        j = symbol_index[sym_b]

        if corr is not None:
            val = float(corr)
            matrix[i][j] = val
            matrix[j][i] = val
        else:
            missing_pairs.append(f"{sym_a}/{sym_b}")

    if missing_pairs:
        logger.warning(
            "[CORR] Missing correlation values for %d pairs: %s",
            len(missing_pairs),
            ", ".join(missing_pairs[:5]),
        )

    return CorrelationMatrix(labels=SYMBOLS, matrix=matrix)


@app.get("/chart/{symbol}", response_model=list[ChartPoint])
async def get_chart(
    symbol: str,
    limit: int = Query(100, ge=1, le=2000),
    window_minutes: Optional[int] = Query(None, ge=1, le=43200),
    session: AsyncSession = Depends(get_session),
):
    """
    Time-series data for charting price and anomaly score.

    Two modes:
      - window_minutes set: return all rows in the trailing window
        (downsampled to <= 1440 points for charting). `limit` is ignored.
      - otherwise: return the most recent `limit` rows (legacy behavior).

    Both modes fetch extra history rows before the display range so the
    composite score can be computed with proper rolling windows for
    every returned data point.
    """
    if symbol not in SYMBOLS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown symbol: {symbol}. Valid: {', '.join(SYMBOLS)}",
        )

    history_padding = 100

    if window_minutes is not None:
        window_stmt = text("""
            SELECT timestamp, symbol, price, return_1m, z_score,
                   ewma_vol, pca_residual, volume
            FROM market_features
            WHERE symbol = :symbol
              AND timestamp >= NOW() - (:minutes * INTERVAL '1 minute')
            ORDER BY timestamp ASC
            LIMIT :cap
        """)
        window_rows = list((await session.execute(window_stmt, {
            "symbol": symbol, "minutes": window_minutes, "cap": 43200,
        })).mappings().all())

        # Warmup history immediately before the window for rolling windows
        hist_stmt = text("""
            SELECT timestamp, symbol, price, return_1m, z_score,
                   ewma_vol, pca_residual, volume
            FROM market_features
            WHERE symbol = :symbol
              AND timestamp < NOW() - (:minutes * INTERVAL '1 minute')
            ORDER BY timestamp DESC
            LIMIT :padding
        """)
        hist_rows = list(reversed((await session.execute(hist_stmt, {
            "symbol": symbol, "minutes": window_minutes, "padding": history_padding,
        })).mappings().all()))

        all_rows = hist_rows + window_rows
        history_start = len(hist_rows)
    else:
        total_fetch = limit + history_padding
        stmt = text("""
            SELECT timestamp, symbol, price, return_1m, z_score,
                   ewma_vol, pca_residual, volume
            FROM market_features
            WHERE symbol = :symbol
            ORDER BY timestamp DESC
            LIMIT :limit
        """)
        with DB_QUERY_SECONDS.labels("/chart/{symbol}").time():
            result = await session.execute(stmt, {"symbol": symbol, "limit": total_fetch})
        all_rows = list(reversed(result.mappings().all()))
        history_start = history_padding if len(all_rows) > history_padding else 0

    if not all_rows:
        return []

    # Split: history rows (for computation) and display rows (to return)
    display_rows = all_rows[history_start:]

    # Compute composite score for each display row
    points: list[ChartPoint] = []
    for i, row in enumerate(display_rows):
        # Global index into all_rows
        global_idx = history_start + i

        # Gather EWMA and PCA history from all rows before this point
        ewma_history = []
        pca_history = []
        for j in range(global_idx):
            prev = all_rows[j]
            if prev.get("ewma_vol") is not None:
                ewma_history.append(float(prev["ewma_vol"]))
            if prev.get("pca_residual") is not None:
                pca_history.append(float(prev["pca_residual"]))

        # Keep only last 100 for the rolling window
        ewma_history = ewma_history[-100:]
        pca_history = pca_history[-100:]

        score = compute_composite_score(
            z_score=row.get("z_score"),
            ewma_vol=row.get("ewma_vol"),
            pca_residual=row.get("pca_residual"),
            ewma_history=ewma_history if ewma_history else None,
            pca_history=pca_history if pca_history else None,
        )

        points.append(ChartPoint(
            timestamp=row["timestamp"],
            price=row.get("price"),
            return_1m=row.get("return_1m"),
            z_score=row.get("z_score"),
            ewma_vol=row.get("ewma_vol"),
            pca_residual=row.get("pca_residual"),
            volume=row.get("volume"),
            composite_score=score,
        ))

    return downsample_points(points)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    logger.info("Cross-Asset Anomaly Detection API starting")
    logger.info("Symbols: %s", ", ".join(SYMBOLS))
    logger.info("Async DB: postgresql+asyncpg://***@%s", os.getenv("DATABASE_URL", "").split("@")[-1])


@app.on_event("shutdown")
async def shutdown():
    logger.info("Cross-Asset Anomaly Detection API shutting down")


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )