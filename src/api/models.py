"""
Pydantic response models for the Cross-Asset Anomaly Detection API.

All models use Pydantic v2 with from_attributes=True for seamless
conversion from SQLAlchemy ORM objects.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Response for GET /health."""
    status: str = Field(..., description="'ok' or 'error'")
    timestamp: datetime = Field(..., description="Server UTC timestamp")
    db: str = Field(..., description="'connected' or error message")


class AssetStatus(BaseModel):
    """Response for a single asset in GET /assets."""
    symbol: str
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