"""
Volatility-regime classification and threshold scaling.

Each symbol's realized volatility is tracked on a short EWMA and
classified against its own trailing distribution: a rolling percentile
of the current vol estimate within the trailing sample history buckets
the symbol into low / medium / high regimes.

Detector thresholds are then scaled by explicit named factors — wider
in high-vol regimes (markets are already choppy; fewer false positives),
tightened in low-vol ones. The underlying detector algorithms are never
changed, only how their thresholds are parameterized.

Pure logic: numpy-free apart from math, deterministic, no I/O.
"""

import logging
import math
from collections import deque
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants — the scaling policy lives here, not in detector logic.
# ---------------------------------------------------------------------------

REGIME_LOW = "low"
REGIME_MEDIUM = "medium"
REGIME_HIGH = "high"

# Effective threshold = base threshold × factor for the active regime.
# High-vol regimes widen thresholds (choppy markets flag more at any
# fixed threshold); low-vol regimes tighten slightly.
REGIME_SCALE_FACTORS = {
    REGIME_LOW: 0.85,
    REGIME_MEDIUM: 1.0,
    REGIME_HIGH: 1.25,
}

# Short-term realized-vol EWMA span, in observations (per symbol).
VOL_EWMA_SPAN = 30
# Trailing distribution: vol estimate sampled every VOL_SAMPLE_EVERY
# observations, retaining VOL_HISTORY_SAMPLES samples per symbol.
VOL_SAMPLE_EVERY = 10
VOL_HISTORY_SAMPLES = 180
# Percentile bounds of the current vol within its trailing distribution.
VOL_PERCENTILE_HIGH = 80
VOL_PERCENTILE_LOW = 20
# Below this many history samples the regime is "medium" (insufficient
# data to claim anything else).
VOL_MIN_SAMPLES = 20


def classify_vol_percentile(
    current: float,
    distribution: list[float],
    low_percentile: int = VOL_PERCENTILE_LOW,
    high_percentile: int = VOL_PERCENTILE_HIGH,
    min_samples: int = VOL_MIN_SAMPLES,
) -> str:
    """
    Bucket `current` by its percentile within `distribution`.

    Returns REGIME_LOW / REGIME_MEDIUM / REGIME_HIGH. With fewer than
    `min_samples` historical points the answer is REGIME_MEDIUM — not
    enough data to claim a regime.
    """
    if not distribution or len(distribution) < min_samples:
        return REGIME_MEDIUM
    below = sum(1 for d in distribution if d < current)
    percentile = 100.0 * below / len(distribution)
    if percentile >= high_percentile:
        return REGIME_HIGH
    if percentile <= low_percentile:
        return REGIME_LOW
    return REGIME_MEDIUM


def scale_for_regime(regime: str) -> float:
    """Explicit threshold multiplier for a regime (1.0 for unknown)."""
    return REGIME_SCALE_FACTORS.get(regime, 1.0)


class VolatilityRegimeTracker:
    """
    Per-symbol rolling realized-vol tracker and regime classifier.

    Feed one price observation per tick via observe(); the tracker
    derives returns, maintains a short-span EWMA vol estimate, samples
    it into a trailing distribution, and classifies the symbol's
    current regime. A regime persists until the next observation.
    """

    def __init__(
        self,
        span: int = VOL_EWMA_SPAN,
        sample_every: int = VOL_SAMPLE_EVERY,
        history_samples: int = VOL_HISTORY_SAMPLES,
    ):
        self.span = span
        self.sample_every = sample_every
        self.history_samples = history_samples
        self._last_price: dict[str, float] = {}
        self._ewma_var: dict[str, float] = {}
        self._count: dict[str, int] = {}
        self._history: dict[str, deque] = {}
        self._regime: dict[str, str] = {}

    def observe(self, symbol: str, price: float) -> str:
        """Feed one tick; returns the symbol's current regime."""
        prev = self._last_price.get(symbol)
        self._last_price[symbol] = price

        if prev is None or prev <= 0 or price is None or price <= 0:
            return self._regime.get(symbol, REGIME_MEDIUM)

        ret = price / prev - 1.0
        alpha = 2.0 / (self.span + 1)
        var = self._ewma_var.get(symbol)
        var = (1 - alpha) * var + alpha * ret * ret if var is not None else ret * ret
        self._ewma_var[symbol] = var

        count = self._count.get(symbol, 0) + 1
        self._count[symbol] = count

        history = self._history.setdefault(
            symbol, deque(maxlen=self.history_samples)
        )
        if count % self.sample_every == 0:
            history.append(math.sqrt(var))

        regime = classify_vol_percentile(math.sqrt(var), list(history))
        self._regime[symbol] = regime
        return regime

    def current_regime(self, symbol: str) -> str:
        """Last classified regime for a symbol (medium before any data)."""
        return self._regime.get(symbol, REGIME_MEDIUM)

    def current_vol(self, symbol: str) -> Optional[float]:
        var = self._ewma_var.get(symbol)
        return math.sqrt(var) if var is not None else None

    def distribution(self, symbol: str) -> list[float]:
        return list(self._history.get(symbol, ()))
