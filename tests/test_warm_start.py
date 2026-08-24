"""Warm-start behavior: consumers resume detection immediately after restart."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.consumers.feature_consumer import (
    SymbolWindows,
    compute_z_score,
    warm_start as feature_warm_start,
)
from src.detection.pipeline import DetectionPipeline


NOW = datetime.now(timezone.utc)


def _feature_records(symbol, n, seed=3):
    rng = np.random.default_rng(seed)
    return [
        {
            "timestamp": NOW - timedelta(minutes=n - i),
            "symbol": symbol,
            "price": 100.0 + i * 0.1,
            "return_1m": float(rng.normal(0, 0.001)),
            "return_5m": None,
            "volume": 1000,
        }
        for i in range(n)
    ]


def test_feature_windows_resume_zscore_immediately():
    windows = SymbolWindows(["BTC-USD"], maxlen=200)
    records = _feature_records("BTC-USD", 30)

    counts = feature_warm_start(windows, lambda: iter(records))

    assert counts == {"BTC-USD": 30}
    assert len(windows.get_returns("BTC-USD")) == 30

    # One live tick after restart -> z-score computable right away
    # (cold start would need 20 more ticks before this returns a value)
    windows.append("BTC-USD", {"return_1m": 0.01})
    assert compute_z_score(windows.get_returns("BTC-USD")) is not None


def test_feature_warm_start_empty_history_is_cold():
    windows = SymbolWindows(["BTC-USD"], maxlen=200)
    counts = feature_warm_start(windows, lambda: [])
    assert counts == {}
    assert len(windows.get_returns("BTC-USD")) == 0


def test_pipeline_warm_start_activates_all_detectors():
    pipe = DetectionPipeline()
    rng = np.random.default_rng(7)

    prices = list(np.cumprod(1 + rng.normal(0, 0.0008, 120)) * 100)
    series = [
        (NOW - timedelta(minutes=len(prices) - i), float(p))
        for i, p in enumerate(prices)
    ]

    counts = pipe.warm_start(prices_by_symbol={"TEST-X": series})
    assert counts == {"TEST-X": 120}

    # Every detector's price window is full
    for d in pipe.detectors:
        assert len(d._price_windows["TEST-X"]) >= d.min_observations, d.name

    # Isolation forest pre-trained -> can predict on the next tick
    iforest = next(d for d in pipe.detectors if d.name == "iforest_price")
    assert "TEST-X" in iforest._models

    # Correlation history rebuilt for the monitored pairs that have data
    corr = next(d for d in pipe.detectors if d.name == "cross_asset_corr")
    # single symbol: no pair has both sides warmed; nothing to assert beyond no-crash
    assert isinstance(corr._corr_history, dict)


def test_pipeline_detects_spike_immediately_after_warm_start():
    pipe = DetectionPipeline()
    rng = np.random.default_rng(11)

    prices = list(np.cumprod(1 + rng.normal(0, 0.0008, 120)) * 100)
    series = [
        (NOW - timedelta(minutes=len(prices) - i), float(p))
        for i, p in enumerate(prices)
    ]
    pipe.warm_start(prices_by_symbol={"BTC-USD": series})

    # First tick after restart is an extreme spike -> detection without warm-up
    now = datetime.now(timezone.utc)
    spike_price = float(prices[-1]) * 1.30
    detections = []
    for _ in range(2):
        detections.extend(pipe.detect("BTC-USD", spike_price, now))
        now = now + timedelta(seconds=30)

    assert detections, "expected immediate detection on a post-warm-start spike"
    types = {d["type"] for d in detections}
    assert "zscore_spike" in types or "isolation_forest_outlier" in types


def test_pipeline_warm_start_skips_invalid_prices():
    pipe = DetectionPipeline()
    series = [
        (NOW - timedelta(minutes=4), 100.0),
        (NOW - timedelta(minutes=3), None),      # invalid
        (NOW - timedelta(minutes=2), float("nan")),  # invalid
        (NOW - timedelta(minutes=1), 101.0),
    ]
    counts = pipe.warm_start(prices_by_symbol={"S": series})
    assert counts["S"] == 2  # only the two valid closes replayed


def test_pipeline_correlation_history_rebuilt_from_two_symbols():
    pipe = DetectionPipeline()
    rng = np.random.default_rng(5)

    base = np.cumprod(1 + rng.normal(0, 0.001, 120)) * 100
    series_a = [(NOW - timedelta(minutes=120 - i), float(p)) for i, p in enumerate(base)]
    noise = base * (1 + rng.normal(0, 0.002, 120))  # correlated with A
    series_b = [(NOW - timedelta(minutes=120 - i), float(p)) for i, p in enumerate(noise)]

    pipe.warm_start(prices_by_symbol={"A": series_a, "B": series_b})

    corr = next(d for d in pipe.detectors if d.name == "cross_asset_corr")
    history = corr._corr_history.get(("^GSPC", "^IXIC"), [])
    # Default pairs reference ^GSPC/^IXIC etc., not A/B — so no history yet.
    # The mechanism under test is that warming real pair symbols works:
    pipe2 = DetectionPipeline(
        # rebuild with default detectors but warm using tracked symbols
    )
    from src.detection.cross_asset import CrossAssetCorrelationDetector

    detector = CrossAssetCorrelationDetector(name="t", min_observations=50)
    pipe2.detectors = [detector]
    pipe2.warm_start(prices_by_symbol={"^GSPC": series_a, "^IXIC": series_b})

    history = detector._corr_history[("^GSPC", "^IXIC")]
    assert len(history) >= 10
    assert all(-1.0 <= h <= 1.0 for h in history)
