"""Engine detector unit tests (pure functions, no database)."""

import numpy as np
import pytest

from src.detection.anomaly_engine import (
    build_description,
    detect_ewma_volatility,
    detect_pca_residual,
    detect_z_score,
)


# --- Z-score detector -------------------------------------------------------

def test_z_score_flags_above_threshold():
    features = [{"z_score": 3.0}]
    flag, signal = detect_z_score(features)
    assert flag
    assert signal == pytest.approx(3.0 / 4.0)


def test_z_score_below_threshold_unflagged_but_scored():
    features = [{"z_score": 1.2}]
    flag, signal = detect_z_score(features)
    assert not flag
    assert signal == pytest.approx(1.2 / 4.0)


def test_z_score_missing_or_empty():
    assert detect_z_score([]) == (False, 0.0)
    assert detect_z_score([{"z_score": None}]) == (False, 0.0)


# --- EWMA detector ----------------------------------------------------------

def test_ewma_flags_spike_over_baseline():
    features = [{"ewma_vol": 0.001}] * 20 + [{"ewma_vol": 0.005}]
    flag, signal = detect_ewma_volatility(features)
    assert flag  # 0.005 > 2.0 * 0.001
    assert signal == pytest.approx(min(0.005 / (0.001 * 3.0), 1.0))


def test_ewma_zero_baseline_positive_current_is_extreme():
    features = [{"ewma_vol": 0.0}] * 10 + [{"ewma_vol": 0.0005}]
    flag, signal = detect_ewma_volatility(features)
    assert flag
    assert signal == pytest.approx(min(0.0005 / 0.001, 1.0))


def test_ewma_no_history_returns_zero():
    features = [{"ewma_vol": 0.01}]
    flag, signal = detect_ewma_volatility(features)
    assert not flag and signal == 0.0


# --- PCA detector -----------------------------------------------------------

def test_pca_flags_large_z():
    history = [{"pca_residual": v} for v in np.abs(np.random.default_rng(1).normal(0.3, 0.1, 50))]
    current = 2.0  # far from history mean
    features = history + [{"pca_residual": current}]
    flag, signal = detect_pca_residual(features)
    assert flag


def test_pca_insufficient_history():
    features = [{"pca_residual": 9.9}] * 3 + [{"pca_residual": 9.9}]
    flag, signal = detect_pca_residual(features)
    assert not flag and signal == 0.0


def test_pca_zero_variance_deviation_counts():
    features = [{"pca_residual": 0.3}] * 10 + [{"pca_residual": 1.3}]
    flag, signal = detect_pca_residual(features)
    assert flag
    assert signal == pytest.approx(min(abs(1.3 - 0.3) / 1.0, 1.0))


# --- Description generator --------------------------------------------------

def test_description_composes_all_flags():
    desc = build_description(
        symbol="BTC-USD",
        z_flag=True, ewma_flag=True, pca_flag=True,
        z_score=-3.2, z_signal=0.8, ewma_signal=0.9, pca_signal=0.5,
        pca_z_val=3.1, ewma_ratio=2.7, price=76000.1234,
    )
    assert "Z-score drop" in desc
    assert "volatility surge (2.7x baseline)" in desc
    assert "cross-asset factor breakdown" in desc
    assert "BTC-USD" in desc
    assert "76000.1234" in desc


def test_description_no_flags_defensive():
    desc = build_description(
        symbol="^GSPC",
        z_flag=False, ewma_flag=False, pca_flag=False,
        z_score=None, z_signal=0.2, ewma_signal=0.1, pca_signal=0.05,
        pca_z_val=None, ewma_ratio=None, price=None,
    )
    assert "Elevated composite score on ^GSPC" in desc
