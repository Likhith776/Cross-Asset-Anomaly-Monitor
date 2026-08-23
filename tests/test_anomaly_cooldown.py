"""Sustained-anomaly suppression (cooldown + escalation) decision logic."""

from datetime import datetime, timedelta, timezone

from src.detection.anomaly_engine import should_insert_event

NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)


def test_first_event_inserts():
    insert, reason = should_insert_event(0.5, None, None, NOW)
    assert insert
    assert "first event" in reason


def test_recent_flat_score_suppressed():
    last_ts = NOW - timedelta(minutes=5)
    insert, reason = should_insert_event(0.50, last_ts, 0.52, NOW)
    assert not insert
    assert "sustained" in reason


def test_recent_lower_score_suppressed():
    last_ts = NOW - timedelta(minutes=5)
    insert, _ = should_insert_event(0.35, last_ts, 0.60, NOW)
    assert not insert


def test_recent_escalated_score_inserts():
    last_ts = NOW - timedelta(minutes=5)
    insert, reason = should_insert_event(0.70, last_ts, 0.60, NOW)
    assert insert
    assert "escalated" in reason


def test_exact_delta_escalates():
    # new >= last + delta (0.10) — equality escalates
    last_ts = NOW - timedelta(minutes=1)
    insert, _ = should_insert_event(0.60, last_ts, 0.50, NOW)
    assert insert


def test_just_under_delta_suppressed():
    last_ts = NOW - timedelta(minutes=1)
    insert, _ = should_insert_event(0.599, last_ts, 0.50, NOW)
    assert not insert


def test_cooldown_elapsed_inserts():
    last_ts = NOW - timedelta(minutes=31)
    insert, reason = should_insert_event(0.50, last_ts, 0.52, NOW)
    assert insert
    assert "cooldown elapsed" in reason


def test_boundary_at_cooldown_suppressed():
    # Exactly at the 30-minute boundary is still inside (age < cooldown fails only past it)
    last_ts = NOW - timedelta(minutes=29)
    insert, _ = should_insert_event(0.50, last_ts, 0.52, NOW)
    assert not insert


def test_missing_last_score_with_recent_timestamp_suppressed():
    # Defensive: recent event but unreadable score -> suppress (not first event)
    last_ts = NOW - timedelta(minutes=5)
    insert, _ = should_insert_event(0.50, last_ts, None, NOW)
    assert not insert
