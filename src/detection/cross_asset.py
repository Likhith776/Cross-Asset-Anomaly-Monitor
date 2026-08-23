"""
Cross-asset correlation break detector.

Monitors rolling correlations between asset pairs and flags
when correlations break down beyond a threshold, which can
indicate regime changes or stress events.
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np

from src.detection.base import BaseDetector, AnomalyEvent

logger = logging.getLogger(__name__)


class CrossAssetCorrelationDetector(BaseDetector):
    """
    Detects anomalous correlation breaks between asset pairs.

    Maintains a rolling window of prices for multiple assets and
    computes pairwise Pearson correlations. When the correlation
    between a monitored pair deviates significantly from its
    historical average, an anomaly is flagged.

    Parameters:
        name: Detector identifier
        window_size: Number of observations in the rolling window (default: 100)
        threshold: Number of std deviations for correlation break (default: 2.5)
        min_observations: Minimum observations before detection is active (default: 50)
        pairs: List of (asset_a, asset_b) tuples to monitor
    """

    def __init__(
        self,
        name: str = "cross_asset_corr",
        window_size: int = 100,
        threshold: float = 2.5,
        min_observations: int = 50,
        pairs: Optional[list[tuple[str, str]]] = None,
    ):
        super().__init__(name=name, window_size=window_size, threshold=threshold)
        self.min_observations = min_observations
        self.pairs = pairs or [
            ("^GSPC", "^IXIC"),   # S&P 500 vs Nasdaq
            ("GC=F", "^GSPC"),    # Gold vs S&P 500
            ("CL=F", "GC=F"),     # Oil vs Gold
            ("BTC-USD", "^GSPC"), # Bitcoin vs S&P 500
        ]
        self._corr_history: dict[tuple[str, str], list[float]] = {}
        self._history_window: int = 200

    def _compute_returns(self, prices: list[float]) -> np.ndarray:
        """Compute simple returns from a price series."""
        arr = np.array(prices)
        return np.diff(arr) / arr[:-1]

    def detect(self, asset: str, price: float, timestamp: datetime) -> Optional[AnomalyEvent]:
        """Check if the new price triggers a correlation break for any monitored pair."""
        self._update_window(asset, price)

        for asset_a, asset_b in self.pairs:
            if asset not in (asset_a, asset_b):
                continue

            prices_a = self._price_windows.get(asset_a)
            prices_b = self._price_windows.get(asset_b)

            if not prices_a or not prices_b:
                continue
            if len(prices_a) < self.min_observations or len(prices_b) < self.min_observations:
                continue

            # Align lengths
            min_len = min(len(prices_a), len(prices_b))
            ret_a = self._compute_returns(prices_a[-min_len:])
            ret_b = self._compute_returns(prices_b[-min_len:])
            common_len = min(len(ret_a), len(ret_b))
            ret_a = ret_a[-common_len:]
            ret_b = ret_b[-common_len:]

            if len(ret_a) < self.min_observations:
                continue

            # Current rolling correlation (last 50 periods)
            rolling_window = min(50, len(ret_a))
            current_corr = np.corrcoef(ret_a[-rolling_window:], ret_b[-rolling_window:])[0, 1]

            if np.isnan(current_corr):
                continue

            # Update correlation history
            pair_key = (asset_a, asset_b)
            if pair_key not in self._corr_history:
                self._corr_history[pair_key] = []
            self._corr_history[pair_key].append(current_corr)
            if len(self._corr_history[pair_key]) > self._history_window:
                self._corr_history[pair_key].pop(0)

            # Check for correlation break
            history = self._corr_history[pair_key]
            if len(history) < 20:
                continue

            hist_arr = np.array(history[:-1])
            hist_mean = np.mean(hist_arr)
            hist_std = np.std(hist_arr, ddof=1)

            if hist_std < 1e-10:
                continue

            corr_z = abs(current_corr - hist_mean) / hist_std

            if corr_z >= self.threshold:
                normalized_score = min(corr_z / (self.threshold * 2), 1.0)
                severity = self._classify_severity(normalized_score)

                return AnomalyEvent(
                    asset=asset,
                    timestamp=timestamp,
                    price=price,
                    score=normalized_score,
                    type="correlation_break",
                    severity=severity,
                    metadata={
                        "detector": self.name,
                        "pair": f"{asset_a}/{asset_b}",
                        "current_correlation": round(float(current_corr), 4),
                        "historical_mean": round(float(hist_mean), 4),
                        "historical_std": round(float(hist_std), 4),
                        "correlation_z_score": round(float(corr_z), 4),
                    },
                )

        return None