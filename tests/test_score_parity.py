"""API composite score must match the detection engine exactly."""

import numpy as np
import pytest

from src.api.main import compute_composite_score
from src.detection.anomaly_engine import score_symbol


def _features(z_hist, e_hist, p_hist, z_cur, e_cur, p_cur):
    features = [
        {"z_score": z, "ewma_vol": e, "pca_residual": p}
        for z, e, p in zip(z_hist, e_hist, p_hist)
    ]
    features.append(
        {"z_score": float(z_cur), "ewma_vol": float(e_cur), "pca_residual": float(p_cur)}
    )
    return features


@pytest.mark.parametrize("seed", range(5))
def test_score_parity_randomized(seed):
    """Randomized windows, including extreme currents that hit edge branches."""
    rng = np.random.default_rng(seed)
    for _ in range(60):
        n = int(rng.integers(6, 100))
        z_hist = rng.normal(0, 1.2, n).tolist()
        e_hist = np.abs(rng.normal(0.001, 0.0004, n)).tolist()
        p_hist = np.abs(rng.normal(0.3, 0.15, n)).tolist()

        z_cur = rng.choice([rng.normal(0, 1.2), 4.5, -3.7, 0.0])
        e_cur = rng.choice([abs(rng.normal(0.001, 0.0004)), 0.0, 0.05])
        p_cur = rng.choice([abs(rng.normal(0.3, 0.15)), 6.0, 0.3])

        features = _features(z_hist, e_hist, p_hist, z_cur, e_cur, p_cur)
        engine_score = score_symbol(features)["anomaly_score"]
        api_score = compute_composite_score(
            z_score=float(z_cur),
            ewma_vol=float(e_cur),
            pca_residual=float(p_cur),
            ewma_history=e_hist,
            pca_history=p_hist,
        )
        assert engine_score == api_score, (
            f"engine={engine_score} api={api_score} "
            f"z={z_cur} e={e_cur} p={p_cur} n={n}"
        )


def test_zero_variance_pca_branch():
    """Flat PCA history with a deviation: engine treats any deviation as notable."""
    p_hist = [0.3] * 10
    features = _features([0.0] * 10, [0.001] * 10, p_hist, 0.0, 0.001, 1.3)
    engine_score = score_symbol(features)["anomaly_score"]
    api_score = compute_composite_score(
        z_score=0.0, ewma_vol=0.001, pca_residual=1.3,
        ewma_history=[0.001] * 10, pca_history=p_hist,
    )
    assert engine_score == api_score
    assert engine_score > 0  # the deviation contributes


def test_zero_baseline_ewma_branch():
    """Zero EWMA baseline with positive current: engine treats vol as extreme."""
    e_hist = [0.0] * 10
    features = _features([0.0] * 10, e_hist, [0.3] * 10, 0.0, 0.0005, 0.3)
    engine_score = score_symbol(features)["anomaly_score"]
    api_score = compute_composite_score(
        z_score=0.0, ewma_vol=0.0005, pca_residual=0.3,
        ewma_history=e_hist, pca_history=[0.3] * 10,
    )
    assert engine_score == api_score
