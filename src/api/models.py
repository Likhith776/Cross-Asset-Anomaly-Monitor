"""
Pydantic response models for the Cross-Asset Anomaly Detection API.

All models use Pydantic v2 with from_attributes=True for seamless
conversion from SQLAlchemy ORM objects.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Response for GET /health."""
    status: str = Field(..., description="'ok' or 'error'")
    timestamp: datetime = Field(..., description="Server UTC timestamp")
    db: str = Field(..., description="'connected' or error message")


class MetaResponse(BaseModel):
    """Response for GET /meta — dataset metadata for dashboard rendering."""
    server_time: datetime = Field(..., description="Server UTC timestamp")
    demo_data_end: Optional[datetime] = Field(
        None,
        description="End of seeded demo data (start of live data). "
                    "None when the database contains only live data.",
    )


class AssetStatus(BaseModel):
    """Response for a single asset in GET /assets."""
    symbol: str
    label: Optional[str] = Field(
        None,
        description="Human-readable name from config/universe.json.",
    )
    price: Optional[float] = None
    z_score: Optional[float] = None
    ewma_vol: Optional[float] = None
    pca_residual: Optional[float] = None
    return_1m: Optional[float] = None
    timestamp: Optional[datetime] = None
    composite_score: float = Field(..., ge=0.0, le=1.0)
    risk_level: str = Field(..., pattern="^(low|medium|high)$")


class AnomalyEventResponse(BaseModel):
    """Response for a single anomaly event."""
    id: int
    timestamp: datetime
    symbol: str
    anomaly_score: float
    z_flag: bool
    ewma_flag: bool
    pca_flag: bool
    description: Optional[str] = None
    macro_context: Optional[str] = Field(
        None,
        description="Scheduled macro release (FOMC/CPI/NFP/...) the anomaly "
                    "coincided with, when within the annotation window.",
    )

    model_config = {"from_attributes": True}


class CorrelationMatrix(BaseModel):
    """Response for GET /correlations — 7×7 symmetric matrix."""
    labels: list[str] = Field(..., min_length=1)
    matrix: list[list[float]]


class ChartPoint(BaseModel):
    """Response for a single data point in GET /chart/{symbol}."""
    timestamp: datetime
    price: Optional[float] = None
    return_1m: Optional[float] = None
    z_score: Optional[float] = None
    ewma_vol: Optional[float] = None
    pca_residual: Optional[float] = None
    volume: Optional[int] = None
    composite_score: float = Field(0.0, ge=0.0, le=1.0)


class AnomalyFeedbackRequest(BaseModel):
    """Request body for POST /anomalies/{id}/feedback."""
    label: Literal["confirmed", "false_positive"]
    note: Optional[str] = Field(
        None,
        max_length=2000,
        description="Free-text note about the judgment.",
    )


class AnomalyFeedbackResponse(BaseModel):
    """Response from POST /anomalies/{id}/feedback."""
    id: int
    anomaly_event_id: int
    label: str
    noted_at: datetime
    note: Optional[str] = None

    model_config = {"from_attributes": True}


class DetectorPrecision(BaseModel):
    """One row of the rolling-precision breakdown."""
    detector: str
    labeled: int
    confirmed: int
    false_positive: int
    precision: Optional[float] = Field(
        None,
        description="confirmed / labeled; None when labeled is 0.",
    )


class AnomalyPrecisionResponse(BaseModel):
    """Response for GET /anomaly-precision."""
    window_days: int
    total_labeled: int
    total_confirmed: int
    total_false_positive: int
    overall_precision: Optional[float] = Field(
        None,
        description="confirmed / labeled across all detectors; None when unlabeled.",
    )
    by_detector: list[DetectorPrecision]