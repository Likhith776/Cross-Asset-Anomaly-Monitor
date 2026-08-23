"""
Async database engine, session factory, and ORM models for the
Cross-Asset Anomaly Detection API.
"""

import os
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/market_anomalies")
ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=300,
    echo=False,
    connect_args={
        "statement_cache_size": 0,
    },
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncSession:
    """FastAPI dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


class Base(DeclarativeBase):
    pass


class MarketFeature(Base):
    __tablename__ = "market_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    price: Mapped[Optional[float]] = mapped_column(Float)
    return_1m: Mapped[Optional[float]] = mapped_column(Float)
    return_5m: Mapped[Optional[float]] = mapped_column(Float)
    z_score: Mapped[Optional[float]] = mapped_column(Float)
    ewma_vol: Mapped[Optional[float]] = mapped_column(Float)
    pca_residual: Mapped[Optional[float]] = mapped_column(Float)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_market_features_timestamp_symbol", "timestamp", "symbol"),
    )


class AnomalyEvent(Base):
    __tablename__ = "anomaly_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    z_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    ewma_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    pca_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_anomaly_events_timestamp", "timestamp"),
        Index("idx_anomaly_events_symbol", "symbol"),
    )


class CorrelationSnapshot(Base):
    __tablename__ = "correlation_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol_a: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol_b: Mapped[str] = mapped_column(String(20), nullable=False)
    correlation: Mapped[Optional[float]] = mapped_column(Float)

    __table_args__ = (
        Index("idx_correlation_snapshots_timestamp", "timestamp"),
        UniqueConstraint(
            "timestamp", "symbol_a", "symbol_b",
            name="uq_corr_snapshot_pair_time",
        ),
    )