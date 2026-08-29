"""
Base detector class providing the interface for all anomaly detectors.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AnomalyEvent:
    """Represents a detected anomaly."""
    asset: str
    timestamp: datetime
    price: float
    score: float
    type: str
    severity: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "asset": self.asset,
            "timestamp": self.timestamp.isoformat(),
            "price": self.price,
            "score": self.score,
            "type": self.type,
            "severity": self.severity,
            "metadata": self.metadata,
        }


class BaseDetector(ABC):
    """Abstract base class for anomaly detectors."""

    def __init__(self, name: str, window_size: int = 100, threshold: float = 0.7):
        self.name = name
        self.window_size = window_size
        self.threshold = threshold
        self._price_windows: dict[str, list[float]] = {}
        # Volatility-regime hooks: the owning pipeline (or the batch
        # engine) records the active regime per asset; detectors scale
        # their thresholds via effective_threshold(). Default scale is
        # 1.0, so without a tracker everything behaves as before.
        self.regime_scale: dict[str, float] = {}
        self.regime_by_asset: dict[str, str] = {}

    def effective_threshold(self, asset: str) -> float:
        """Base threshold scaled by the asset's active volatility regime."""
        return self.threshold * self.regime_scale.get(asset, 1.0)

    def current_regime(self, asset: str) -> str:
        return self.regime_by_asset.get(asset, "unknown")

    def _update_window(self, asset: str, price: float) -> list[float]:
        """Add a price to the sliding window for an asset."""
        if asset not in self._price_windows:
            self._price_windows[asset] = []
        window = self._price_windows[asset]
        window.append(price)
        if len(window) > self.window_size:
            window.pop(0)
        return window

    def _classify_severity(self, score: float) -> str:
        """Classify anomaly severity based on score."""
        if score >= 0.9:
            return "critical"
        elif score >= 0.7:
            return "high"
        elif score >= 0.5:
            return "medium"
        return "low"

    @abstractmethod
    def detect(self, asset: str, price: float, timestamp: datetime) -> Optional[AnomalyEvent]:
        """
        Run detection on a single price point.

        Returns an AnomalyEvent if an anomaly is detected, otherwise None.
        """
        ...

    def reset(self, asset: Optional[str] = None) -> None:
        """Reset price windows. If asset is None, reset all."""
        if asset:
            self._price_windows.pop(asset, None)
        else:
            self._price_windows.clear()