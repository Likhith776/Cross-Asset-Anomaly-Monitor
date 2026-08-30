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


def test_detector_from_description_joint():
    """THE regression test for the original gap: the multivariate joint
    detector's feedback was attributed to 'unknown' because the shared
    attribution mapping lacked its anomaly type."""
    description = (
        "Tick-level joint_mahalanobis (joint_mahalanobis detector), "
        "d2/dim=6.99 detected on ^GSPC — critical severity"
    )
    assert detector_from_description(description) == "joint"


def test_attribution_mapping_covers_every_known_detector_type():
    from src.precision import _DETECTOR_KEYWORDS

    assert _DETECTOR_KEYWORDS == {
        "zscore_spike": "zscore",
        "isolation_forest_outlier": "iforest",
        "correlation_break": "correlation",
        "joint_mahalanobis": "joint",
    }


def test_compute_precision_attributes_joint_feedback_to_joint():
    labels = [
        _label(1, "confirmed", days_ago=1,
               description="... joint_mahalanobis ..."),
        _label(2, "false_positive", days_ago=1,
               description="... joint_mahalanobis ..."),
        _label(3, "confirmed", days_ago=1,
               description="... zscore_spike ..."),
    ]
    r = compute_precision(labels, window_days=30)
    by_det = {d["detector"]: d for d in r["by_detector"]}
    assert "unknown" not in by_det
    assert by_det["joint"]["labeled"] == 2
    assert by_det["joint"]["precision"] == 0.5
    assert by_det["zscore"]["labeled"] == 1


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


def test_anomaly_response_parses_lead_lag_from_marker():
    """The API model carries the structured lead-lag hint when the
    description carries the marker (same read-time parse as livesite)."""
    from src.api.models import AnomalyEventResponse
    from src.detection.lead_lag import extract_lead_lag

    description = (
        "Tick-level zscore_spike — high severity "
        "[lead-lag: led by GC=F (2 ticks, r=0.71)]"
    )
    event = AnomalyEventResponse(
        id=1, timestamp=datetime.now(timezone.utc), symbol="BTC-USD",
        anomaly_score=0.9, z_flag=True, ewma_flag=False, pca_flag=False,
        description=description,
        lead_lag=extract_lead_lag(description),
    )
    assert event.lead_lag == {"leader": "GC=F", "lag_ticks": 2, "correlation": 0.71}


def test_anomaly_response_lead_lag_none_without_marker():
    from src.api.models import AnomalyEventResponse
    from src.detection.lead_lag import extract_lead_lag

    event = AnomalyEventResponse(
        id=1, timestamp=datetime.now(timezone.utc), symbol="BTC-USD",
        anomaly_score=0.9, z_flag=True, ewma_flag=False, pca_flag=False,
        description="Tick-level zscore_spike — high severity",
        lead_lag=extract_lead_lag("Tick-level zscore_spike — high severity"),
    )
    assert event.lead_lag is None


def test_anomaly_endpoints_resolve_lead_lag_at_call_time():
    """
    Regression test for a call-time NameError: both /anomalies endpoints
    called extract_lead_lag(r["description"]) without importing it —
    import-time checks (and direct AnomalyEventResponse construction)
    never exercised this path. Drives the real endpoint functions with
    a fake session so the actual route code runs.
    """
    import asyncio

    from src.api import main as api_main

    row = {
        "id": 1,
        "timestamp": datetime.now(timezone.utc),
        "symbol": "BTC-USD",
        "anomaly_score": 0.9,
        "z_flag": True,
        "ewma_flag": False,
        "pca_flag": False,
        "description": (
            "Tick-level zscore_spike — high severity "
            "[lead-lag: led by GC=F (2 ticks, r=0.71)]"
        ),
        "macro_context": None,
        "llm_explanation": None,
    }

    class _FakeMappings:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return _FakeMappings(self._rows)

    class _FakeSession:
        def __init__(self, rows):
            self._rows = rows

        async def execute(self, stmt, params=None):
            return _FakeResult(self._rows)

    session = _FakeSession([row])

    by_symbol = asyncio.run(
        api_main.get_anomalies_by_symbol(symbol="BTC-USD", days=7, session=session)
    )
    assert by_symbol[0].lead_lag == {
        "leader": "GC=F", "lag_ticks": 2, "correlation": 0.71,
    }

    all_events = asyncio.run(
        api_main.get_anomalies(limit=50, min_score=0.0, session=session)
    )
    assert all_events[0].lead_lag == {
        "leader": "GC=F", "lag_ticks": 2, "correlation": 0.71,
    }


def test_anomaly_endpoints_lead_lag_none_without_marker():
    row = {
        "id": 1,
        "timestamp": datetime.now(timezone.utc),
        "symbol": "BTC-USD",
        "anomaly_score": 0.9,
        "z_flag": True,
        "ewma_flag": False,
        "pca_flag": False,
        "description": "Tick-level zscore_spike — high severity",
        "macro_context": None,
        "llm_explanation": None,
    }

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def all(self):
            return self._rows

    class _FakeSession:
        def __init__(self, rows):
            self._rows = rows

        async def execute(self, stmt, params=None):
            return _FakeResult(self._rows)

    import asyncio

    from src.api import main as api_main

    out = asyncio.run(
        api_main.get_anomalies_by_symbol(symbol="BTC-USD", days=7, session=_FakeSession([row]))
    )
    assert out[0].lead_lag is None
