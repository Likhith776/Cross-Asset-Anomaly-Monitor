"""
Market data consumers.

This package contains modules for consuming market data from Kafka,
running feature engineering and anomaly detection, and persisting
results to PostgreSQL.

Modules:
    feature_consumer: Consume ticks, compute features, write market_features
    anomaly_consumer: Consume ticks, run tick-level detection, write anomaly_events
    base: Abstract async base class for consumers
"""

from src.consumers.base import BaseConsumer

__all__ = ["BaseConsumer"]
