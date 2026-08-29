"""Volatility-regime classifier, tracker, and threshold scaling.

Offline and deterministic: synthetic series, no DB, no network.
"""

from datetime import datetime, timezone

import numpy as np
import pytest

from src.detection.regime import (
    REGIME_HIGH,
    REGIME_LOW,
    REGIME_MEDIUM,
    REGIME_SCALE_FACTORS,
    VOL_MIN_SAMPLES,
    VolatilityRegimeTracker,
    classify_vol_percentile,
    scale_for_regime,
)
from src.detection.zscore import ZScoreDetector


# ---------------------------------------------------------------------------
# Constants

def test_scale_factors_are_explicit_and_ordered():
    assert REGIME_SCALE_FACTORS[REGIME_LOW] < REGIME_SCALE_FACTORS[REGIME_MEDIUM]
    assert REGIME_SCALE_FACTORS[REGIME_MEDIUM] == 1.0
    assert REGIME_SCALE_FACTORS[REGIME_HIGH] > REGIME_SCALE_FACTORS[REGIME_MEDIUM]


def test_scale_for_regime_unknown_defaults_to_neutral():
    assert scale_for_regime("nonexistent") == 1.0


# ---------------------------------------------------------------------------
# classify_vol_percentile

def test_high_percentile_vol_classifies_high():
    dist = [1.0] * 50
    assert classify_vol_percentile(2.0, dist) == REGIME_HIGH


def test_low_percentile_vol_classifies_low():
    dist = [1.0] * 50
    assert classify_vol_percentile(0.5, dist) == REGIME_LOW


def test_mid_percentile_classifies_medium():
    dist = list(np.linspace(0.5, 1.5, 100))
    assert classify_vol_percentile(1.0, dist) == REGIME_MEDIUM


def test_insufficient_samples_defaults_to_medium():
    assert classify_vol_percentile(2.0, [1.0] * 5) == REGIME_MEDIUM
    assert classify_vol_percentile(2.0, []) == REGIME_MEDIUM
    assert classify_vol_percentile(2.0, [1.0] * (VOL_MIN_SAMPLES - 1)) == REGIME_MEDIUM


# ---------------------------------------------------------------------------
# Tracker

def _tracker():
    return VolatilityRegimeTracker()


def test_tracker_starts_medium_and_ignores_first_tick():
    t = _tracker()
    assert t.observe("X", 100.0) == REGIME_MEDIUM  # no prior price → no return
    assert t.current_regime("X") == REGIME_MEDIUM


def test_tracker_flags_high_vol_on_regime_shift():
    """A classifier measures current vol against its own trailing
    distribution, so a HIGH regime means vol *rose* above recent
    history: quiet phase fills the baseline, burst shifts the regime."""
    rng = np.random.default_rng(11)
    t = _tracker()
    price = 100.0
    for _ in range(300):                       # quiet baseline fills history
        price *= 1 + float(rng.normal(0, 0.001))
        t.observe("X", price)
    for _ in range(300):                       # regime shift: 10× the vol
        price *= 1 + float(rng.normal(0, 0.01))
        t.observe("X", price)
    assert t.current_regime("X") == REGIME_HIGH


def test_tracker_flags_low_vol_in_a_quiet_regime():
    t = _tracker()
    price = 100.0
    # 200 heavy ticks (fills the history with high-vol samples),
    # then 200 ultra-quiet ticks → current vol collapses to the floor.
    rng = np.random.default_rng(5)
    for _ in range(200):
        price *= 1 + float(rng.normal(0, 0.01))
        t.observe("X", price)
    for _ in range(200):
        price *= 1 + float(rng.normal(0, 0.0005))
        t.observe("X", price)
    assert t.current_regime("X") == REGIME_LOW


# ---------------------------------------------------------------------------
# Threshold scaling — sensitivity differs by regime

def _windowed_detector(prices, symbol="TEST-X"):
    d = ZScoreDetector(name="t", window_size=100, threshold=3.0, min_observations=20)
    for p in prices:
        d._update_window(symbol, p)
    return d


def _regime_prices(seed, n=100):
    rng = np.random.default_rng(seed)
    return list(100.0 + rng.normal(0, 0.1, n))


def test_effective_threshold_scales_with_regime():
    d = _windowed_detector(_regime_prices(3))
    d.regime_scale["TEST-X"] = REGIME_SCALE_FACTORS[REGIME_HIGH]
    assert d.effective_threshold("TEST-X") == pytest.approx(3.75)
    d.regime_scale["TEST-X"] = REGIME_SCALE_FACTORS[REGIME_LOW]
    assert d.effective_threshold("TEST-X") == pytest.approx(2.55)


def test_same_spike_flags_in_low_vol_but_not_in_high_vol():
    """A spike whose z sits between the low-vol and high-vol thresholds
    is caught in a calm regime and ignored in a choppy one — the
    entire point of regime scaling."""
    window = _regime_prices(9)
    mean, std = float(np.mean(window)), float(np.std(window, ddof=1))
    spike = mean + 3.0 * std   # z == 3.0: above 3.0*0.85, below 3.0*1.25

    low = _windowed_detector(window)
    low.regime_scale["TEST-X"] = REGIME_SCALE_FACTORS[REGIME_LOW]
    low.regime_by_asset["TEST-X"] = REGIME_LOW
    high = _windowed_detector(window)
    high.regime_scale["TEST-X"] = REGIME_SCALE_FACTORS[REGIME_HIGH]
    high.regime_by_asset["TEST-X"] = REGIME_HIGH

    ts = datetime.now(timezone.utc)
    low_event = low.detect("TEST-X", spike, ts)
    high_event = high.detect("TEST-X", spike, ts)

    assert low_event is not None, "low-vol regime should catch a 3.0σ spike"
    assert high_event is None, "high-vol regime should not catch a 3.0σ spike"
    assert low_event.metadata["regime"] == REGIME_LOW
    assert low_event.metadata["threshold"] == pytest.approx(2.55)


def test_pipeline_feeds_regime_to_detectors():
    from datetime import datetime, timezone

    from src.detection.pipeline import DetectionPipeline

    pipe = DetectionPipeline()
    ts = datetime.now(timezone.utc)
    pipe.detect("TEST-X", 100.0, ts)
    pipe.detect("TEST-X", 100.0, ts)

    # Every detector saw the regime bookkeeping for TEST-X.
    for d in pipe.detectors:
        assert d.regime_by_asset.get("TEST-X") in (REGIME_MEDIUM, REGIME_LOW, REGIME_HIGH)
        assert d.regime_scale.get("TEST-X") == pytest.approx(
            REGIME_SCALE_FACTORS[d.regime_by_asset["TEST-X"]]
        )


# ---------------------------------------------------------------------------
# Batch engine: scaled thresholds via score_symbol

def _engine_features(ewma_baseline, ewma_current):
    return [
        {"z_score": 0.5, "ewma_vol": ewma_baseline, "pca_residual": 0.0}
        for _ in range(20)
    ] + [
        {"z_score": 0.5, "ewma_vol": ewma_current, "pca_residual": 0.0}
    ]


def test_engine_ewma_threshold_scales_with_regime():
    from src.detection.anomaly_engine import detect_ewma_volatility

    # current = 2.1 × baseline: flags at the default 2.0 multiplier,
    # does not flag once the high-vol regime widens it to 2.5.
    features = _engine_features(ewma_baseline=0.001, ewma_current=0.0021)

    flagged_default, _ = detect_ewma_volatility(features)
    flagged_high, _ = detect_ewma_volatility(
        features, multiplier=2.0 * REGIME_SCALE_FACTORS[REGIME_HIGH]
    )
    assert flagged_default is True
    assert flagged_high is False


def test_engine_score_symbol_records_regime():
    from src.detection.anomaly_engine import score_symbol

    result = score_symbol(
        _engine_features(ewma_baseline=0.001, ewma_current=0.003),
        regime=REGIME_HIGH,
    )
    assert result["regime"] == REGIME_HIGH
