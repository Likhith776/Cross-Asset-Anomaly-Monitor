"""Backtest harness behavior: injection, panel alignment, replay, scoring.

All tests are offline and deterministic — no yfinance, no network.
"""

import numpy as np
import pandas as pd
import pytest

from src.backtest.data import align_panel
from src.backtest.harness import replay_panel, score_trial
from src.backtest.inject import PlantedEvent, plant_anomalies
from src.detection.pipeline import DetectionPipeline


def _series(n=300, seed=1, start="2026-01-01"):
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    rng = np.random.default_rng(seed)
    prices = 100 * np.cumprod(1 + rng.normal(0, 0.0008, n))
    return pd.Series(prices, index=idx, name="s")


def _panel(n_rows=320, cols=("A", "B"), seed=3):
    idx = pd.date_range("2026-01-01", periods=n_rows, freq="h", tz="UTC")
    rng = np.random.default_rng(seed)
    data = {
        c: 100 * np.cumprod(1 + rng.normal(0, 0.0008, n_rows)) for c in cols
    }
    return pd.DataFrame(data, index=idx)


# ---------------------------------------------------------------------------
# Injection


def test_plant_anomalies_count_kinds_and_warmup_intact():
    closes = _series(900).to_numpy()
    out, events = plant_anomalies(
        closes, n_events=6, magnitude_sigma=6.0, warmup=150, seed=11
    )

    assert len(events) == 6
    assert [e.start for e in events] == sorted(e.start for e in events)
    assert all(e.start >= 150 for e in events)
    # Kinds cycle spike -> dip -> vol_burst
    assert [e.kind for e in events] == [
        "spike", "dip", "vol_burst", "spike", "dip", "vol_burst",
    ]
    # Warm-up region untouched; length preserved; only planted region differs
    assert np.allclose(out[:150], closes[:150])
    assert len(out) == len(closes)
    changed = np.nonzero(~np.isclose(out, closes))[0]
    assert all(any(e.start <= i <= e.end for e in events) for i in changed)


def test_plant_places_feasible_maximum_when_series_short():
    closes = _series(400).to_numpy()
    _, events = plant_anomalies(
        closes, n_events=10, magnitude_sigma=6.0, warmup=150, seed=11
    )
    # 80-tick separation over positions 150..320 fits at most 3 events;
    # placement must deliver that maximum deterministically.
    assert 0 < len(events) < 10
    for a, b in zip(events, events[1:]):
        assert b.start - a.end > 50


def test_planted_spike_magnitude_matches_level_sigma():
    closes = _series(400).to_numpy()
    level_sigma = float(np.std(closes[:150]))
    out, events = plant_anomalies(
        closes, n_events=1, magnitude_sigma=8.0, warmup=150, seed=2
    )
    ev = next(e for e in events if e.kind == "spike")
    move = out[ev.start] - closes[ev.start - 1]
    assert move == pytest.approx(8.0 * level_sigma, rel=1e-9)


def test_plant_positions_never_overlap():
    closes = _series(900).to_numpy()
    _, events = plant_anomalies(
        closes, n_events=10, magnitude_sigma=4.0, warmup=150, seed=5
    )
    for a, b in zip(events, events[1:]):
        assert b.start - a.end > 50  # windows cannot share statistics


def test_plant_on_short_series_returns_fewer_events():
    closes = _series(160).to_numpy()  # barely longer than warmup
    out, events = plant_anomalies(
        closes, n_events=5, magnitude_sigma=4.0, warmup=150, seed=7
    )
    assert len(out) == len(closes)
    assert len(events) < 5


# ---------------------------------------------------------------------------
# Panel alignment


def test_align_panel_keeps_common_timestamps_only():
    s1 = _series(100, seed=1)
    s2 = _series(100, seed=2)[10:]  # starts later
    panel = align_panel({"A": s1, "B": s2})
    assert list(panel.columns) == ["A", "B"]
    assert panel.index.equals(s2.index)  # intersection = later index


def test_align_panel_unifies_offset_grids():
    # Real exchanges stamp hourly bars at different minute offsets
    # (equities :30, futures :00) — flooring onto one grid must align them.
    idx = pd.date_range("2026-01-01", periods=50, freq="h", tz="UTC")
    rng = np.random.default_rng(4)
    on_hour = pd.Series(
        100 * np.cumprod(1 + rng.normal(0, 0.001, 50)), index=idx, name="F"
    )
    off_grid = on_hour.copy()
    off_grid.index = idx + pd.Timedelta(minutes=30)
    off_grid.name = "E"

    panel = align_panel({"E": off_grid, "F": on_hour}, freq="h")

    assert len(panel) == 50
    assert panel.notna().all().all()


# ---------------------------------------------------------------------------
# Replay


def test_replay_panel_clean_noise_alert_rate_bounded():
    panel = _panel(n_rows=320)
    pipe = DetectionPipeline(aggregate=False)

    alerts = replay_panel(panel, pipe, warmup_ticks=150)

    # Percent-level alert rates on ordinary noise are EXPECTED here:
    # IsolationForest flags ~contamination (5%) of ticks by construction,
    # and the level-based z-score compares each tick against the window's
    # internal spread, which underestimates forward deviation on trending
    # series (~7% empirically). What must hold: no storm, and no alert
    # ever originates from the warm-up region.
    eval_ticks = (len(panel) - 150) * len(panel.columns)
    assert len(alerts) / eval_ticks <= 0.25
    assert all(a["_tick"] >= 150 for a in alerts)


def test_replay_panel_catches_large_spike_at_labeled_position():
    panel = _panel(n_rows=320)
    work = panel.copy()
    work.iloc[250, work.columns.get_loc("A")] *= 1.10  # +10% bad print

    pipe = DetectionPipeline(aggregate=False)
    alerts = replay_panel(work, pipe, warmup_ticks=150)

    hits = [
        a for a in alerts
        if a["_sym"] == "A" and abs(a["_tick"] - 250) <= 5
        and a["type"] in ("zscore_spike", "isolation_forest_outlier")
    ]
    assert hits, "expected the planted +10% print to be detected"


def test_replay_panel_rejects_short_panel():
    with pytest.raises(ValueError):
        replay_panel(_panel(n_rows=100), DetectionPipeline(), warmup_ticks=150)


# ---------------------------------------------------------------------------
# Scoring


def _alert(type_, tick, asset="A", pair=None):
    meta = {"pair": pair} if pair else {}
    return {"type": type_, "asset": asset, "metadata": meta, "_tick": tick}


def test_score_trial_matching_precision_and_recall():
    events = [PlantedEvent(100, 102, "spike", 8.0)]
    alerts = [
        _alert("zscore_spike", 100),          # hit
        _alert("zscore_spike", 101),          # duplicate on same event
        _alert("isolation_forest_outlier", 500),  # nowhere near
    ]

    scored = score_trial(alerts, events, target="A")

    z = scored["zscore_spike"]
    assert z["alerts"] == 2
    assert z["matched_events"] == 1
    assert z["recall"] == 1.0
    assert z["precision"] == 0.5  # one real catch, one duplicate
    assert z["median_lag"] == 0

    iso = scored["isolation_forest_outlier"]
    assert iso["matched_events"] == 0
    assert iso["precision"] == 0.0
    assert iso["recall"] == 0.0


def test_score_trial_correlation_alert_attributes_via_pair():
    events = [PlantedEvent(200, 200, "dip", 8.0)]
    alerts = [
        # Fires while processing B but names the A/X pair — counts for target A
        {"type": "correlation_break", "asset": "B",
         "metadata": {"pair": "A/X"}, "_tick": 203},
    ]

    scored = score_trial(alerts, events, target="A")

    corr = scored["correlation_break"]
    assert corr["matched_events"] == 1
    assert corr["median_lag"] == 3


def test_score_trial_miss_beyond_lag_margin():
    events = [PlantedEvent(100, 100, "spike", 4.0)]
    alerts = [_alert("zscore_spike", 110)]  # outside start..end+5

    scored = score_trial(alerts, events, target="A")
    assert scored["zscore_spike"]["matched_events"] == 0
