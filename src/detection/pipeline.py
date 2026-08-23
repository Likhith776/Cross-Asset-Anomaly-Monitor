"""
Detection pipeline that orchestrates multiple detectors.

Runs all configured detectors on each incoming price point
and aggregates the results.
"""

import logging
from datetime import datetime
from typing import Any

from src.detection.base import BaseDetector, AnomalyEvent
from src.detection.zscore import ZScoreDetector
from src.detection.isolation_forest import IsolationForestDetector
from src.detection.cross_asset import CrossAssetCorrelationDetector

logger = logging.getLogger(__name__)


class DetectionPipeline:
    """
    Orchestrates multiple anomaly detectors.

    Each incoming price tick is passed through all registered detectors.
    If multiple detectors flag the same asset, the highest-scoring anomaly
    is returned (or all anomalies if aggregate=False).

    Parameters:
        aggregate: If True, return only the highest-scoring anomaly per tick.
                   If False, return all detected anomalies.
    """

    def __init__(self, aggregate: bool = True):
        self.aggregate = aggregate
        self.detectors: list[BaseDetector] = []
        self._setup_default_detectors()

    def _setup_default_detectors(self) -> None:
        """Initialize the default set of detectors."""
        self.detectors = [
            ZScoreDetector(
                name="zscore_price",
                window_size=100,
                threshold=3.0,
                min_observations=20,
            ),
            ZScoreDetector(
                name="zscore_volume",
                window_size=50,
                threshold=2.5,
                min_observations=15,
            ),
            IsolationForestDetector(
                name="iforest_price",
                window_size=100,
                threshold=0.05,
                min_observations=30,
                retrain_interval=50,
            ),
            CrossAssetCorrelationDetector(
                name="cross_asset_corr",
                window_size=100,
                threshold=2.5,
                min_observations=50,
            ),
        ]
        logger.info(
            "Initialized %d detectors: %s",
            len(self.detectors),
            [d.name for d in self.detectors],
        )

    def add_detector(self, detector: BaseDetector) -> None:
        """Add a custom detector to the pipeline."""
        self.detectors.append(detector)
        logger.info("Added detector: %s", detector.name)

    def remove_detector(self, name: str) -> None:
        """Remove a detector by name."""
        self.detectors = [d for d in self.detectors if d.name != name]
        logger.info("Removed detector: %s", name)

    def detect(
        self,
        asset: str,
        price: float,
        timestamp: datetime,
    ) -> list[dict[str, Any]]:
        """
        Run all detectors on a price point.

        Args:
            asset: Asset symbol (e.g., "BTC-USD")
            price: Current price
            timestamp: Timestamp of the price observation

        Returns:
            List of anomaly dictionaries. If aggregate=True, contains at most
            one entry (the highest-scoring anomaly).
        """
        anomalies: list[AnomalyEvent] = []

        for detector in self.detectors:
            try:
                event = detector.detect(asset=asset, price=price, timestamp=timestamp)
                if event is not None:
                    anomalies.append(event)
                    logger.debug(
                        "Detector %s flagged anomaly for %s: score=%.4f",
                        detector.name,
                        asset,
                        event.score,
                    )
            except Exception as e:
                logger.error(
                    "Error in detector %s for asset %s: %s",
                    detector.name,
                    asset,
                    e,
                    exc_info=True,
                )

        if self.aggregate and anomalies:
            anomalies.sort(key=lambda a: a.score, reverse=True)
            anomalies = anomalies[:1]

        return [a.to_dict() for a in anomalies]

    def get_detector_info(self) -> list[dict[str, Any]]:
        """Return information about all registered detectors."""
        return [
            {
                "name": d.name,
                "type": d.__class__.__name__,
                "window_size": d.window_size,
                "threshold": d.threshold,
                "assets_tracked": list(d._price_windows.keys()),
            }
            for d in self.detectors
        ]