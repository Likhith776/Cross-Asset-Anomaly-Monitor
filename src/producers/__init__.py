"""
Market data producers.

This package contains modules for ingesting real-time and historical
market data and publishing to Kafka.

Modules:
    market_data_producer: Poll Yahoo Finance and publish ticks to Kafka
    base: Abstract async base class for producers
"""

from src.producers.base import BaseProducer

__all__ = ["BaseProducer"]
