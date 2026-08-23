"""
Isolation Forest based anomaly detector.

Uses a sliding window of recent price changes to detect
anomalous market behavior via tree-based isolation.
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np
from sklearn.ensemble import IsolationForest

from src.detection.base import BaseDetector, AnomalyEvent

logger = logging.getLogger(__name__)


class IsolationForestDetector(BaseDetector):
    """
    Isolation Forest anomaly detector.

    Constructs features from the sliding price window (returns, volatility,
    range ratio) and uses IsolationForest to identify outliers.

    Parameters:
        name: Detector identifier
        window_size: Number of observations in the rolling window (default: 100)
        threshold: Contamination parameter for IsolationForest (default: 0.05)
        min_observations: Minimum observations before detection is active (default: 30)
        retrain_interval: Retrain model every N new observations (default: 50)
    """

    def __init__(
        self,
        name: str = "isolation_forest",
        window_size: int = 100,
        threshold: float = 0.05,
        min_observations: int = 30,
        retrain_interval: int = 50,
    ):
        super().__init__(name=name, window_size=window_size, threshold=threshold)
        self.min_observations = min_observations
        self.retrain_interval = retrain_interval
        self._models: dict[str, IsolationForest] = {}
        self._observation_counts: dict[str, int] = {}

    def _extract_features(self, prices: np.ndarray) -> np.ndarray:
        """
        Extract features from a price series.

        All three feature columns are aligned to the returns array
        (length = len(prices) - 1): the return, its absolute value, and a
        5-period rolling RMS volatility. Positions earlier than the 5th
        use an expanding-window RMS so no NaNs or padding are introduced.
        """
        returns = np.diff(prices) / prices[:-1]

        if len(returns) == 0:
            return np.column_stack([returns, returns, returns])

        rolling = 5
        squared = returns ** 2
        vol = np.empty_like(returns)

        # Expanding-window RMS for the first `rolling - 1` positions
        for i in range(min(rolling - 1, len(returns))):
            vol[i] = np.sqrt(np.mean(squared[: i + 1]))

        # Rolling RMS for the rest (constant-time via cumulative sums)
        if len(returns) >= rolling:
            csum = np.cumsum(np.concatenate([[0.0], squared]))
            vol[rolling - 1:] = np.sqrt((csum[rolling:] - csum[:-rolling]) / rolling)

        return np.column_stack([returns, np.abs(returns), vol])

    def detect(self, asset: str, price: float, timestamp: datetime) -> Optional[AnomalyEvent]:
        window = self._update_window(asset, price)

        if len(window) < self.min_observations:
            return None

        arr = np.array(window)
        features = self._extract_features(arr)

        # Retrain model periodically
        obs_count = self._observation_counts.get(asset, 0) + 1
        self._observation_counts[asset] = obs_count

        if asset not in self._models or obs_count % self.retrain_interval == 0:
            self._models[asset] = IsolationForest(
                contamination=self.threshold,
                n_estimators=100,
                max_samples="auto",
                random_state=42,
                n_jobs=-1,
            )
            self._models[asset].fit(features)
            logger.debug("Retrained IsolationForest for %s (obs=%d)", asset, obs_count)

        # Predict on the full window; check the last point
        predictions = self._models[asset].predict(features)
        scores = self._models[asset].score_samples(features)

        last_pred = predictions[-1]
        last_score = scores[-1]

        if last_pred == -1:
            # Convert anomaly score to 0-1 range (lower score = more anomalous)
            normalized_score = min(max(0.5 - last_score, 0.0), 1.0)
            severity = self._classify_severity(normalized_score)

            return AnomalyEvent(
                asset=asset,
                timestamp=timestamp,
                price=price,
                score=normalized_score,
                type="isolation_forest_outlier",
                severity=severity,
                metadata={
                    "detector": self.name,
                    "raw_score": round(float(last_score), 4),
                    "window_size": len(window),
                    "observation_count": obs_count,
                },
            )

        return None