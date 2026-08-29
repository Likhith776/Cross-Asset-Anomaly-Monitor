"""Livesite FileStore feedback round-trip: (symbol, timestamp) keyed,
most-recent-wins, precision math on the loaded data."""

import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from src.livesite import LiveSite
from src.livesite_seed import seed_history
from src.producers.composite_provider import CompositeProvider
from src.producers.data_provider import MarketDataProvider
from src.detection.anomaly_engine import SYMBOLS


def _dry_provider():
    """A no-op provider so the cycle runs without HTTP calls."""
    class _Empty:
        symbols = []

        def fetch_all(self):
            return []
    return CompositeProvider(
        symbols=SYMBOLS, primaries=[_Empty()], secondary=MarketDataProvider(SYMBOLS),
        attempts=1, retry_wait=0,
    )


def test_record_feedback_replaces_prior_label_for_same_key():
    from src.livesite import FileStore

    store = FileStore()
    a = store.record_feedback("BTC-USD", "2026-08-26T10:00:00+00:00", "confirmed")
    b = store.record_feedback("BTC-USD", "2026-08-26T10:00:00+00:00", "false_positive")

    assert len(store.feedback) == 1
    assert store.feedback[0]["label"] == "false_positive"
    assert store.feedback[0]["noted_at"] != a["noted_at"]  # re-stamped on replace


def test_record_feedback_caps_at_500():
    from src.livesite import FileStore

    store = FileStore()
    base = datetime(2026, 8, 26, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(550):
        # unique timestamp per iteration (minute + second)
        ts = (base + timedelta(seconds=i)).isoformat()
        store.record_feedback("S", ts, "confirmed")
    assert len(store.feedback) == 500
    # Newest is at index 0
    assert store.feedback[0]["timestamp"] > store.feedback[-1]["timestamp"]


def test_livesite_publishes_feedback_and_precision_artifacts():
    with tempfile.TemporaryDirectory() as td:
        state_dir = td + "/branch"
        out_dir = td + "/build"
        site = LiveSite(SYMBOLS, _dry_provider(), state_dir, out_dir)

        # Drop one of the live anomalies so we have a real (symbol,
        # timestamp) to label, then publish.
        if site.store.anomalies:
            target = site.store.anomalies[0]
            site.store.record_feedback(
                target["symbol"], target["timestamp"],
                "confirmed", "labeled in test",
            )
        site.save_state()
        site.build_artifacts()

        feedback_data = json.load(open(out_dir + "/data/feedback.json"))
        assert feedback_data["schema"] == 1
        assert all(
            "symbol" in f and "timestamp" in f and "label" in f
            for f in feedback_data["feedback"]
        )

        precision = json.load(open(out_dir + "/data/precision.json"))
        assert precision["window_days"] == 30
        assert "by_detector" in precision
        assert "overall_precision" in precision


def test_livesite_loaded_feedback_round_trips_through_state_file():
    with tempfile.TemporaryDirectory() as td:
        state_dir = td + "/branch"
        out_dir = td + "/build"

        # First cycle: seed + record one label + publish.
        first = LiveSite(SYMBOLS, _dry_provider(), state_dir, out_dir)
        if first.store.anomalies:
            target = first.store.anomalies[0]
            first.store.record_feedback(
                target["symbol"], target["timestamp"],
                "confirmed", "first"
            )
        first.save_state()
        first.build_artifacts()

        # Second cycle: fresh LiveSite must see the prior label.
        second = LiveSite(SYMBOLS, _dry_provider(), state_dir, out_dir)
        if first.store.anomalies:
            sym, ts = target["symbol"], target["timestamp"]
            match = next(
                (f for f in second.store.feedback
                 if f["symbol"] == sym and f["timestamp"] == ts),
                None,
            )
            assert match is not None, "feedback did not round-trip through state/"
            assert match["label"] == "confirmed"
            assert match["note"] == "first"
