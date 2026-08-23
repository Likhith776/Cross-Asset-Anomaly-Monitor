"""
Anomaly detection modules.

This package contains the detection algorithms and orchestration
logic for identifying market anomalies across asset classes.

Modules:
    pipeline: Orchestration layer that runs all detectors
    zscore: Z-score based statistical anomaly detection
    isolation_forest: Isolation forest based anomaly detection
    cross_asset: Cross-asset correlation break detection
    base: Abstract base class for all detectors
"""

from src.detection.pipeline import DetectionPipeline

__all__ = ["DetectionPipeline"]