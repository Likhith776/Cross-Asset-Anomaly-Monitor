"""Scheduler daily-cycle timing logic."""

from datetime import datetime, timedelta, timezone

from src.detection.scheduler import next_daily_time


def test_before_target_hour_today():
    now = datetime(2026, 8, 23, 5, 30, tzinfo=timezone.utc)
    assert next_daily_time(now, 1) == datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc)


def test_after_target_hour_rolls_to_tomorrow():
    now = datetime(2026, 8, 23, 2, 0, tzinfo=timezone.utc)
    assert next_daily_time(now, 1) == datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc)


def test_exactly_at_target_rolls_forward():
    # At the boundary itself the next occurrence is tomorrow's
    now = datetime(2026, 8, 23, 1, 0, 0, tzinfo=timezone.utc)
    assert next_daily_time(now, 1) == now + timedelta(days=1)


def test_cleanup_and_backup_hours_are_distinct_cycles():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    cleanup = next_daily_time(now, 0)
    backup = next_daily_time(now, 1)
    assert cleanup < backup
    assert (backup - cleanup) == timedelta(hours=1)
