"""Macro-calendar annotation: window logic, description augmentation,
and end-to-end annotation through the slim tick path.

Timestamps are derived from the loaded calendar itself, so tests never
go stale as the calendar file is updated.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.detection.macro_calendar import (
    augment_description,
    load_calendar,
    macro_event_for,
    reload_calendar,
)


@pytest.fixture(scope="module")
def calendar():
    return reload_calendar()


def test_calendar_loads_sorted_with_valid_entries(calendar):
    assert len(calendar) > 0
    names = {e["name"] for e in calendar}
    assert {"FOMC rate decision", "CPI release",
            "NFP (employment situation)"} <= names
    for a, b in zip(calendar, calendar[1:]):
        assert a["timestamp"] <= b["timestamp"]


def test_inside_window_annotates_with_event_name(calendar):
    event = calendar[0]
    ts = event["timestamp"] + timedelta(minutes=10)
    hit = macro_event_for(ts, calendar=calendar)
    assert hit is not None
    assert hit["name"] == event["name"]
    assert hit["delta_minutes"] == 10


def test_outside_window_returns_none(calendar):
    event = calendar[0]
    ts = event["timestamp"] + timedelta(minutes=45)  # window default 30
    assert macro_event_for(ts, calendar=calendar) is None


def test_window_boundary_is_inclusive(calendar):
    event = calendar[0]
    at_edge = macro_event_for(event["timestamp"] + timedelta(minutes=30), calendar=calendar)
    assert at_edge is not None
    assert at_edge["delta_minutes"] == 30


def test_nearest_event_wins_when_two_overlap():
    synthetic = [
        {"name": "event A", "timestamp": datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)},
        {"name": "event B", "timestamp": datetime(2030, 1, 1, 12, 20, tzinfo=timezone.utc)},
    ]
    ts = datetime(2030, 1, 1, 12, 15, tzinfo=timezone.utc)
    hit = macro_event_for(ts, window_minutes=30, calendar=synthetic)
    assert hit["name"] == "event B"       # 5m away vs A's 15m
    assert hit["delta_minutes"] == 5


def test_augment_description_appends_marker(calendar):
    event = calendar[0]
    ts = event["timestamp"] + timedelta(minutes=7)
    out = augment_description("Tick-level zscore_spike — high severity", ts)
    assert out.startswith("Tick-level zscore_spike — high severity")
    assert f"[macro: {event['name']} ±7m]" in out


def test_augment_description_untouched_outside_window(calendar):
    ts = calendar[0]["timestamp"] + timedelta(minutes=59)
    desc = "Tick-level zscore_spike — high severity"
    assert augment_description(desc, ts) == desc


def test_naive_timestamp_treated_as_utc(calendar):
    event = calendar[0]
    naive = event["timestamp"].replace(tzinfo=None) + timedelta(minutes=5)
    assert macro_event_for(naive, calendar=calendar) is not None


# ---------------------------------------------------------------------------
# End-to-end through the slim tick path (fake storage, real pipeline)

def _make_app_with_capture():
    import threading

    from src.consumers.feature_consumer import SymbolWindows
    from src.detection.pipeline import DetectionPipeline
    from src.slim import SlimApp

    app = SlimApp.__new__(SlimApp)
    app.symbols = ["TEST-X"]
    app.stop_event = threading.Event()
    app.message_count = 0
    app.provider = None
    app.windows = SymbolWindows(app.symbols, maxlen=200)
    app.writer = type(
        "W", (),
        {
            "write_feature": lambda self, rec: True,
            "write_correlation_snapshot": lambda self, ts, pairs: True,
        },
    )()  # feature writes recorded nowhere; detection capture is below
    app.pipeline = DetectionPipeline()
    app.persisted = []
    app._last_event = lambda symbol: (None, None)
    app._persist_anomaly = lambda params: app.persisted.append(params)
    return app


def _quote(price, ts):
    return {
        "symbol": "TEST-X", "price": price,
        "return_1m": None, "return_5m": None, "volume": 1000,
        "timestamp": ts.isoformat(),
    }


def test_tick_path_annotates_anomaly_inside_calendar_window(calendar):
    app = _make_app_with_capture()

    # Quiet ramp into the FOMC window, then a spike 10 minutes after
    # the first calendar event.
    event = calendar[0]
    base = event["timestamp"] - timedelta(minutes=20)
    for i in range(30):
        ts = base + timedelta(minutes=i)
        app.process_tick(_quote(100.0 + i * 0.01, ts))
    spike_ts = event["timestamp"] + timedelta(minutes=10)
    app.process_tick(_quote(125.0, spike_ts))

    assert app.persisted, "expected the spike to be detected"
    params = app.persisted[-1]
    assert params[1] == "TEST-X"
    assert params[7] == event["name"]                    # macro_context column
    assert "[macro:" in params[6]                        # description marker
    assert "±10m" in params[6]


def test_tick_path_outside_window_leaves_macro_context_none(calendar):
    app = _make_app_with_capture()

    # Same shape, but the spike lands 2 hours away from any event.
    event = calendar[0]
    base = event["timestamp"] + timedelta(hours=2)
    for i in range(30):
        ts = base + timedelta(minutes=i)
        app.process_tick(_quote(100.0 + i * 0.01, ts))
    app.process_tick(_quote(125.0, base + timedelta(minutes=30)))

    assert app.persisted
    assert all(p[7] is None for p in app.persisted)
    assert all("[macro:" not in p[6] for p in app.persisted)


def test_livesite_persist_carries_macro_context(calendar):
    from src.livesite import LiveSite, FileStore

    site = LiveSite.__new__(LiveSite)
    site.symbols = ["TEST-X"]
    site.state_dir = "unused"
    site.out_dir = "unused"
    site.store = FileStore()
    site._persist_anomaly(
        (
            datetime.now(timezone.utc), "TEST-X", 0.9,
            True, False, False,
            "Tick-level zscore_spike — high severity [macro: FOMC rate decision ±3m]",
            "FOMC rate decision",
        )
    )
    assert site.store.anomalies[0]["macro_context"] == "FOMC rate decision"
