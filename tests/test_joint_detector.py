"""
MultivariateJointDetector — the joint-only coverage proof.

The centerpiece: a synthetic 3-symbol panel where the final
cross-section is a genuine JOINT anomaly (a tightly-correlated pair
moves in opposite directions — each asset an unremarkable ~1σ move)
that no single-asset detector flags, caught by the joint detector and
only by it.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.detection.cross_asset import CrossAssetCorrelationDetector
from src.detection.isolation_forest import IsolationForestDetector
from src.detection.joint import MultivariateJointDetector
from src.detection.pipeline import DetectionPipeline
from src.detection.zscore import ZScoreDetector


SYMBOLS = ["AAA", "BBB", "CCC"]
JOINT_NAME = "joint_mahalanobis"


def _correlated_panel(
    n_sections: int,
    rho: float = 0.9995,
    sigma: float = 0.001,
    seed: int = 42,
) -> list[dict[str, float]]:
    """
    n_sections cross-sections of per-symbol returns. AAA and BBB move
    together with correlation ~rho; CCC is independent. Returns are
    expressed as multipliers: price_t = price_{t-1} * (1 + r).
    """
    rng = np.random.default_rng(seed)
    cov = np.array([
        [sigma**2, rho * sigma**2, 0.0],
        [rho * sigma**2, sigma**2, 0.0],
        [0.0, 0.0, (sigma * 1.3) ** 2],
    ])
    returns = rng.multivariate_normal([0, 0, 0], cov, size=n_sections)
    return [{s: 1.0 + float(r) for s, r in zip(SYMBOLS, row)} for row in returns]


def _prices_from_returns(section_returns, base=100.0):
    """Cumulative prices per symbol from per-section returns."""
    prices = []
    current = {s: base for s in SYMBOLS}
    for section in section_returns:
        current = {s: current[s] * section[s] for s in SYMBOLS}
        prices.append(dict(current))
    return prices


def _feed(pipe, prices, start_hour=0):
    """Feed cross-sections through a pipeline; returns events per section."""
    events = []
    t0 = datetime(2030, 6, 1, 12, 0, tzinfo=timezone.utc)
    for i, section in enumerate(prices):
        ts = t0 + timedelta(hours=i + start_hour)
        for s in SYMBOLS:
            found = pipe.detect(s, section[s], ts)
            for e in found:
                events.append((i, s, e))
    return events


def _single_asset_pipeline():
    """Everything EXCEPT the joint detector — the coverage baseline."""
    pipe = DetectionPipeline()
    pipe.detectors = [
        ZScoreDetector(name="zscore_price", window_size=100, threshold=3.0,
                       min_observations=20),
        ZScoreDetector(name="zscore_volume", window_size=50, threshold=2.5,
                       min_observations=15),
        IsolationForestDetector(name="iforest_price", window_size=100,
                                threshold=0.05, min_observations=30,
                                retrain_interval=50),
        CrossAssetCorrelationDetector(name="cross_asset_corr", window_size=100,
                                      threshold=2.5, min_observations=50,
                                      pairs=[("AAA", "BBB")]),
    ]
    return pipe


def _full_pipeline():
    pipe = _single_asset_pipeline()
    pipe.add_detector(MultivariateJointDetector(
        name=JOINT_NAME, symbols=SYMBOLS, threshold=3.0,
        min_observations=50, window_size=100,
    ))
    return pipe


def test_construction_defaults_follow_universe():
    d = MultivariateJointDetector()
    from src.universe import load_universe

    assert d.symbols == list(load_universe().symbols)


def test_incomplete_cross_section_scores_nothing():
    d = MultivariateJointDetector(symbols=SYMBOLS, min_observations=5)
    ts = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert d.detect("AAA", 100.0, ts) is None       # 1 of 3 symbols
    assert d.detect("BBB", 100.0, ts) is None       # 2 of 3
    assert d.detect("CCC", 100.0, ts) is None       # complete, but no returns yet


def test_cold_start_returns_none_until_min_observations():
    d = MultivariateJointDetector(symbols=SYMBOLS, threshold=3.0, min_observations=50)
    prices = _prices_from_returns(_correlated_panel(60))
    events = _feed(d if isinstance(d, DetectionPipeline) else _wrap(d), prices[:55])
    assert all(e[2].type != JOINT_NAME for e in events)


def _wrap(detector):
    """Tiny adapter so _feed can drive a bare detector."""
    class _Single:
        def detect(self, asset, price, ts):
            return detector.detect(asset, price, ts) or []
    return _Single()


def test_the_joint_only_anomaly():
    """THE coverage proof: an opposite-direction move on a tight pair —
    ~1σ per asset (invisible to every single-asset detector), jointly
    impossible. The joint detector must catch it, alone."""
    history_sections = _correlated_panel(70, seed=7)
    prices = _prices_from_returns(history_sections)

    # The anomalous cross-section: AAA +0.7σ, BBB −0.7σ (opposite on a
    # ρ=0.9995 pair), CCC flat. Individually: a 0.7σ move is utterly
    # ordinary — below every single-asset threshold and inside iForest's
    # in-sample spread. Jointly: ~31σ along the pair's
    # near-zero-variance difference direction (d²/dim ≈ 6 ≫ 3.0).
    sigma = 0.001
    last = prices[-1]
    anomaly_section = {
        "AAA": last["AAA"] * (1.0 + 0.7 * sigma),
        "BBB": last["BBB"] * (1.0 - 0.7 * sigma),
        "CCC": last["CCC"],
    }
    prices.append(anomaly_section)

    full = _full_pipeline()
    baseline = _single_asset_pipeline()

    full_events = _feed(full, prices)
    baseline_events = _feed(baseline, prices)

    # What happened on the final (anomalous) cross-section?
    last_idx = len(prices) - 1
    full_at_anomaly = [e for i, s, e in full_events if i == last_idx]
    baseline_at_anomaly = [e for i, s, e in baseline_events if i == last_idx]

    assert any(e["type"] == JOINT_NAME for e in full_at_anomaly), (
        "the joint detector must catch the opposite-direction pair move"
    )
    assert baseline_at_anomaly == [], (
        "no single-asset detector may flag this cross-section"
    )
    joint_event = next(e for e in full_at_anomaly if e["type"] == JOINT_NAME)
    assert 0.0 <= joint_event["score"] <= 1.0
    assert joint_event["metadata"]["regime"] in ("low", "medium", "high", "unknown")
    assert joint_event["metadata"]["mahalanobis_d2"] > 3.0 * joint_event["metadata"]["dimension"]


def test_normal_panel_stays_quiet():
    """Same pipeline, purely correlated history: no joint alarms."""
    prices = _prices_from_returns(_correlated_panel(80, seed=13))
    full = _full_pipeline()
    events = _feed(full, prices)
    assert not [e for _, _, e in events if e["type"] == JOINT_NAME]
