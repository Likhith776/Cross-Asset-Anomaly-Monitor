"""Anomaly feedback + rolling-precision logic. Offline, no DB."""

from datetime import datetime, timedelta, timezone

import pytest

from src.precision import compute_precision, detector_from_description


def _now():
    return datetime.now(timezone.utc)


def _label(event_id, label, days_ago, description="Tick-level zscore_spike"):
    ts = _now() - timedelta(days=days_ago)
    return {
        "anomaly_event_id": event_id,
        "label": label,
        "noted_at": ts.isoformat(),
        "description": description,
    }


# ---------------------------------------------------------------------------
# detector attribution (description keyword lookup)

def test_detector_from_description_zscore():
    assert detector_from_description("Tick-level zscore_spike (zscore_price) — high") == "zscore"


def test_detector_from_description_iforest():
    assert detector_from_description("Tick-level isolation_forest_outlier (...) — critical") == "iforest"


def test_detector_from_description_correlation():
    assert detector_from_description("Tick-level correlation_break (...) — low") == "correlation"


def test_detector_from_description_unknown():
    assert detector_from_description(None) == "unknown"
    assert detector_from_description("some new detector not yet keyed") == "unknown"


# ---------------------------------------------------------------------------
# compute_precision — the math that drives both the API endpoint and the
# livesite publisher. These tests cover empty, simple, deduplication, and
# the rolling-window cut.

def test_empty_window_returns_zeros():
    r = compute_precision([], window_days=30)
    assert r["total_labeled"] == 0
    assert r["total_confirmed"] == 0
    assert r["total_false_positive"] == 0
    assert r["overall_precision"] is None
    assert r["by_detector"] == []


def test_simple_two_confirmed_one_rejected():
    labels = [
        _label(1, "confirmed", days_ago=1, description="... zscore_spike ..."),
        _label(2, "confirmed", days_ago=2, description="... zscore_spike ..."),
        _label(3, "false_positive", days_ago=3, description="... zscore_spike ..."),
    ]
    r = compute_precision(labels, window_days=30)
    assert r["total_labeled"] == 3
    assert r["total_confirmed"] == 2
    assert r["total_false_positive"] == 1
    assert r["overall_precision"] == pytest.approx(2 / 3, rel=1e-3)
    assert len(r["by_detector"]) == 1
    assert r["by_detector"][0]["detector"] == "zscore"
    assert r["by_detector"][0]["precision"] == pytest.approx(2 / 3, rel=1e-3)


def test_deduplicates_to_most_recent_label_per_event():
    labels = [
        _label(1, "confirmed",        days_ago=5, description="... zscore_spike ..."),
        _label(1, "false_positive",   days_ago=2, description="... zscore_spike ..."),  # newer
    ]
    r = compute_precision(labels, window_days=30)
    assert r["total_labeled"] == 1
    assert r["total_confirmed"] == 0
    assert r["total_false_positive"] == 1


def test_rolling_window_excludes_old_labels():
    labels = [
        _label(1, "confirmed", days_ago=2, description="... zscore_spike ..."),  # in
        _label(2, "confirmed", days_ago=45, description="... zscore_spike ..."),  # out
    ]
    r = compute_precision(labels, window_days=30)
    assert r["total_labeled"] == 1
    assert r["total_confirmed"] == 1


def test_per_detector_breakdown():
    labels = [
        _label(1, "confirmed",        days_ago=1, description="... zscore_spike ..."),
        _label(2, "false_positive",   days_ago=1, description="... zscore_spike ..."),
        _label(3, "confirmed",        days_ago=1,
               description="... isolation_forest_outlier ..."),
        _label(4, "confirmed",        days_ago=1,
               description="... isolation_forest_outlier ..."),
    ]
    r = compute_precision(labels, window_days=30)
    by_det = {d["detector"]: d for d in r["by_detector"]}
    assert by_det["zscore"]["labeled"] == 2
    assert by_det["zscore"]["precision"] == 0.5
    assert by_det["iforest"]["labeled"] == 2
    assert by_det["iforest"]["precision"] == 1.0


def test_unparseable_noted_at_dropped_not_crashed():
    labels = [
        _label(1, "confirmed", days_ago=1, description="... zscore_spike ..."),
        {"anomaly_event_id": 99, "label": "confirmed", "noted_at": "not-a-date",
         "description": "..."},
    ]
    r = compute_precision(labels, window_days=30)
    assert r["total_labeled"] == 1  # only the well-formed row


def test_blank_description_attributes_to_unknown():
    labels = [
        _label(1, "confirmed", days_ago=1, description=""),
    ]
    r = compute_precision(labels, window_days=30)
    assert r["by_detector"][0]["detector"] == "unknown"
