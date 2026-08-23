"""Isolation forest feature extraction and full pipeline behavior."""

from datetime import datetime, timezone

import numpy as np
import pytest

from src.detection.isolation_forest import IsolationForestDetector
from src.detection.pipeline import DetectionPipeline


@pytest.mark.parametrize("n", [2, 5, 12, 100])
def test_feature_extraction_aligned_and_finite(n):
    det = IsolationForestDetector(name="t", window_size=100, min_observations=5)
    rng = np.random.default_rng(7)
    prices = 100 * np.cumprod(1 + rng.normal(0, 0.001, n))

    feats = det._extract_features(prices)

    assert feats.shape == (n - 1, 3)
    assert np.isfinite(feats).all()


def test_feature_extraction_volatility_magnitudes():
    det = IsolationForestDetector(name="t", window_size=100, min_observations=5)
    # 5 flat prices then a jump — vol column should be ~0 then positive
    prices = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 110.0])
    feats = det._extract_features(prices)

    returns = np.diff(prices) / prices[:-1]
    assert feats[0, 0] == pytest.approx(0.0)
    assert feats[-1, 0] == pytest.approx(returns[-1])
    assert feats[-1, 2] > feats[0, 2]  # vol after the jump exceeds the flat start


def test_pipeline_detects_injected_spike():
    pipe = DetectionPipeline()
    rng = np.random.default_rng(7)
    now = datetime.now(timezone.utc)

    detections = []
    for i in range(120):
        price = 100 + rng.normal(0, 0.05)
        if i == 100:
            price = 130.0  # hard spike
        detections.extend(pipe.detect("BTC-USD", float(price), now))

    assert len(detections) > 0
    types = {d["type"] for d in detections}
    assert "zscore_spike" in types
    for d in detections:
        assert 0.0 <= d["score"] <= 1.0
        assert d["severity"] in ("low", "medium", "high", "critical")


def test_pipeline_quiet_before_min_observations():
    pipe = DetectionPipeline()
    now = datetime.now(timezone.utc)
    detections = []
    for _ in range(10):  # below every detector's minimum
        detections.extend(pipe.detect("GC=F", 100.0, now))
    assert detections == []
