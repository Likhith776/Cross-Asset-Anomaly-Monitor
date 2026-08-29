"""
Multivariate joint-anomaly detector (Mahalanobis distance).

Scores each cross-section of the full symbol set jointly, catching
departures that are invisible to any single-asset detector: e.g. two
tightly-correlated assets suddenly moving in opposite directions —
each individually an unremarkable 1σ move, jointly impossible.

Why Mahalanobis distance rather than PCA reconstruction error:
- No component-count hyperparameter. PCA requires choosing how many
  components span "normal" — a second tuning knob that changes what is
  and isn't anomalous.
- The covariance matrix absorbs per-asset scale differences and their
  correlations natively; no feature normalization step to get wrong.
- The squared distance of a normal vector is chi-square distributed
  with dim degrees of freedom, so the default threshold has a direct
  interpretation: mean per-dimension squared deviation (d²/dim ≈ 3.0
  is far in the tail of chi²(dim)/dim, whose mean is 1.0).
- Closed-form: mean and (shrunk) covariance straight from the rolling
  window, no iterative fitting — fewer ways to implement it wrong.
The one weakness, covariance singularity when the window barely exceeds
the dimension, is handled with diagonal shrinkage.

Feature vector per cross-section: for each tracked symbol, its most
recent per-tick return and running EWMA vol estimate (both derived
from the price stream this detector already receives).

Cold start: no scoring until `min_observations` complete cross-sections
have accumulated — consistent with the other detectors' warm-up.

The detector participates in DetectionPipeline's aggregate-highest-
score selection like every other detector; no special casing.
"""

import logging
import math
from collections import deque
from datetime import datetime
from typing import Any, Optional

import numpy as np

from src.detection.base import AnomalyEvent, BaseDetector
from src.universe import load_universe

logger = logging.getLogger(__name__)

# Diagonal shrinkage added to the covariance as a fraction of its mean
# variance — floors the matrix against singularity when the window is
# small relative to the joint dimension.
COV_SHRINKAGE_FRACTION = 0.05


class MultivariateJointDetector(BaseDetector):
    """
    Mahalanobis-distance anomaly detector over the full symbol set.

    Parameters:
        name: Detector identifier
        symbols: Tracked symbols (default: config/universe.json)
        threshold: Flag when the mean per-dimension squared Mahalanobis
            deviation (d²/dim) exceeds this value. d²/dim is ~1.0 on
            average for in-distribution points, so 3.0 is deep in the
            tail.
        min_observations: Complete cross-sections required before
            scoring activates (also must exceed the joint dimension).
        window_size: Rolling window of joint observations backing the
            mean/covariance estimates.
    """

    def __init__(
        self,
        name: str = "joint_mahalanobis",
        symbols: Optional[list[str]] = None,
        threshold: float = 3.0,
        min_observations: int = 50,
        window_size: int = 100,
        vol_span: int = 30,
    ):
        super().__init__(name=name, window_size=window_size, threshold=threshold)
        self.symbols: list[str] = list(symbols) if symbols else list(
            load_universe().symbols
        )
        self.min_observations = min_observations
        self.vol_span = vol_span

        # Per-symbol latest prices/timestamps forming the cross-section.
        self._latest_price: dict[str, float] = {}
        self._latest_ts: dict[str, datetime] = {}
        # Per-symbol EWMA variance of that symbol's own returns.
        self._ewma_var: dict[str, float] = {}
        # Rolling joint feature vectors, oldest first.
        self._joint_window: deque = deque(maxlen=window_size)
        self._last_joint_ts: Optional[datetime] = None

    # ------------------------------------------------------------------

    def detect(self, asset: str, price: float, timestamp: datetime) -> Optional[AnomalyEvent]:
        if asset not in self.symbols:
            return None  # not part of this detector's joint universe

        window = self._update_window(asset, price)

        # Per-symbol EWMA vol from that symbol's own consecutive returns.
        self._update_symbol_vol(asset, window)

        prev_price = self._latest_price.get(asset)
        self._latest_price[asset] = price
        self._latest_ts[asset] = timestamp

        # A joint observation needs every tracked symbol present, and
        # fresh: each must have ticked at or after the last joint build.
        if any(s not in self._latest_price for s in self.symbols):
            return None
        oldest_pending = min(self._latest_ts.values())
        if self._last_joint_ts is not None and oldest_pending < self._last_joint_ts:
            return None  # waiting for a stale symbol to catch up
        if any(len(self._price_windows[s]) < 2 for s in self.symbols):
            return None  # first cross-section has no return yet

        vector = self._build_vector()
        self._joint_window.append(vector)
        self._last_joint_ts = max(self._latest_ts.values())
        # Require a fresh full cross-section before the next joint
        # observation — without this, the next symbol's tick would pair
        # its new price with every other symbol's stale one.
        self._latest_price.clear()

        return self._score(asset, price, timestamp)

    # ------------------------------------------------------------------

    def _update_symbol_vol(self, asset: str, window: list[float]) -> None:
        if len(window) < 2:
            return
        ret = window[-1] / window[-2] - 1.0
        alpha = 2.0 / (self.vol_span + 1)
        var = self._ewma_var.get(asset)
        self._ewma_var[asset] = (
            (1 - alpha) * var + alpha * ret * ret if var is not None else ret * ret
        )

    def _build_vector(self) -> np.ndarray:
        values: list[float] = []
        for s in self.symbols:
            prices = self._price_windows[s]
            ret = prices[-1] / prices[-2] - 1.0 if len(prices) >= 2 else 0.0
            values.append(ret)
            values.append(math.sqrt(self._ewma_var.get(s, 0.0)))
        return np.array(values)

    def _score(self, asset: str, price: float, timestamp: datetime) -> Optional[AnomalyEvent]:
        if len(self._joint_window) < self.min_observations:
            return None

        matrix = np.array(self._joint_window)
        dim = matrix.shape[1]
        if len(matrix) <= dim + 1:
            return None  # not enough observations to estimate covariance

        mean = matrix.mean(axis=0)
        cov = np.cov(matrix, rowvar=False)
        floor = COV_SHRINKAGE_FRACTION * float(np.trace(cov)) / dim
        cov = cov + np.eye(dim) * (floor + 1e-18)

        diff = matrix[-1] - mean
        try:
            d2 = float(diff @ np.linalg.solve(cov, diff))
        except np.linalg.LinAlgError:
            logger.warning("[%s] covariance solve failed — skipping tick", self.name)
            return None
        if not np.isfinite(d2) or d2 < 0:
            return None

        mean_dev = math.sqrt(d2 / dim)          # avg joint sigmas per dimension
        mean_dev_squared = d2 / dim             # the flagged statistic
        threshold = self.effective_threshold(asset)
        if mean_dev_squared <= threshold:
            return None

        normalized_score = min(mean_dev_squared / (threshold * 2), 1.0)
        severity = self._classify_severity(normalized_score)

        logger.info(
            "[%s] joint anomaly on %s: d²=%.1f dim=%d d²/dim=%.2f (threshold %.2f)",
            self.name, asset, d2, dim, d2 / dim, threshold,
        )
        return AnomalyEvent(
            asset=asset,
            timestamp=timestamp,
            price=price,
            score=round(normalized_score, 4),
            type="joint_mahalanobis",
            severity=severity,
            metadata={
                "detector": self.name,
                "mahalanobis_d2": round(d2, 2),
                "dimension": dim,
                "mean_deviation": round(mean_dev, 3),
                "threshold": round(threshold, 4),
                "window_observations": len(self._joint_window),
                "regime": self.current_regime(asset),
            },
        )
