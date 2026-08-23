"""
Z-Score based anomaly detector.

Detects price movements that deviate significantly from the
recent rolling mean and standard deviation.
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np

from src.detection.base import BaseDetector, AnomalyEvent

logger = logging.getLogger(__name__)


class ZScoreDetector(BaseDetector):
    """
    Z-Score anomaly detector.

    An anomaly is flagged when the absolute z-score of the current price
    relative to the rolling window exceeds a configurable threshold.

    Parameters:
        name: Detector identifier
        window_size: Number of observations in the rolling window (default: 100)
        threshold: Z-score threshold for anomaly flagging (default: 3.0)
        min_observations: Minimum observations before detection is active (default: 20)
    """

    def __init__(
        self,
        name: str = "zscore",
        window_size: int = 100,
        threshold: float = 3.0,
        min_observations: int = 20,
    ):
        super().__init__(name=name, window_size=window_size, threshold=threshold)
        self.min_observations = min_observations

    def detect(self, asset: str, price: float, timestamp: datetime) -> Optional[AnomalyEvent]:
        window = self._update_window(asset, price)

        if len(window) < self.min_observations:
            return None

        arr = np.array(window[:-1])  # Exclude current price
        mean = np.mean(arr)
        std = np.std(arr, ddof=1)

        if std < 1e-10:
            return None

        z_score = abs((price - mean) / std)

        if z_score >= self.threshold:
            # Normalize score to 0-1 range for consistency across detectors
            normalized_score = min(z_score / (self.threshold * 2), 1.0)
            severity = self._classify_severity(normalized_score)

            return AnomalyEvent(
                asset=asset,
                timestamp=timestamp,
                price=price,
                score=normalized_score,
                type="zscore_spike",
                severity=severity,
                metadata={
                    "detector": self.name,
                    "z_score": round(z_score, 4),
                    "rolling_mean": round(mean, 4),
                    "rolling_std": round(std, 4),
                    "window_size": len(window),
                },
            )

        return None