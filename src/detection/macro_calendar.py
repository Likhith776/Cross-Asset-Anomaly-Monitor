"""
Economic-calendar context for anomaly annotations.

A scheduled macro release (FOMC decision, CPI, NFP, ...) landing near
a detected anomaly is context, not suppression: this module tells the
pipeline WHEN an anomaly coincides with a known calendar event so the
record can carry that fact. Detection behavior is never altered.

The calendar itself is a static, version-controlled file
(config/economic_calendar.json) that must be updated manually as new
schedules are published — it is deliberately NOT a live API feed. See
the note inside the file.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

CALENDAR_PATH = os.path.join(
    # file is src/detection/macro_calendar.py → repo root is three up
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "economic_calendar.json",
)

MACRO_WINDOW_MINUTES = int(os.getenv("MACRO_WINDOW_MINUTES", "30"))

_calendar_cache: Optional[list[dict[str, Any]]] = None


def load_calendar(path: str = CALENDAR_PATH, force_reload: bool = False) -> list[dict[str, Any]]:
    """
    Load and sort the static calendar (oldest first). Cached per process;
    pass force_reload=True (or call reload_calendar) after editing.
    """
    global _calendar_cache
    if _calendar_cache is not None and not force_reload:
        return _calendar_cache
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        events = []
        for e in payload.get("events", []):
            try:
                events.append({
                    "name": str(e["name"]),
                    "timestamp": datetime.fromisoformat(
                        str(e["timestamp"]).replace("Z", "+00:00")
                    ),
                })
            except (KeyError, ValueError) as ex:
                logger.warning("[MACRO] skipping malformed calendar entry %s: %s", e, ex)
        events.sort(key=lambda e: e["timestamp"])
        _calendar_cache = events
        logger.info("[MACRO] loaded %d calendar events from %s", len(events), path)
    except (OSError, ValueError) as ex:
        logger.error("[MACRO] calendar unavailable at %s: %s", path, ex)
        _calendar_cache = []
    return _calendar_cache


def reload_calendar(path: str = CALENDAR_PATH) -> list[dict[str, Any]]:
    """Force a re-read (used by tests and after manual calendar edits)."""
    return load_calendar(path, force_reload=True)


def macro_event_for(
    ts: datetime,
    window_minutes: Optional[int] = None,
    calendar: Optional[list[dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    """
    The calendar event within ±window_minutes of `ts`, or None.

    If several events fall inside the window (rare), the nearest wins.
    Naive timestamps are treated as UTC.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    events = calendar if calendar is not None else load_calendar()
    if not events:
        return None

    window = MACRO_WINDOW_MINUTES if window_minutes is None else window_minutes
    best: Optional[dict[str, Any]] = None
    for event in events:
        delta_seconds = abs((ts - event["timestamp"]).total_seconds())
        if delta_seconds <= window * 60:
            minutes = round(delta_seconds / 60)
            if best is None or minutes < best["delta_minutes"]:
                best = {"name": event["name"], "delta_minutes": minutes}
    return best


def augment_description(description: str, ts: datetime) -> str:
    """
    Append the macro-context marker when `ts` coincides with a calendar
    event; otherwise return the description unchanged.

    Marker format: ` [macro: FOMC rate decision ±7m]` — a fixed prefix
    that downstream parsers can spot without breaking the existing
    severity tail (`— high severity`) which precedes it.
    """
    event = macro_event_for(ts)
    if event is None:
        return description
    marker = f" [macro: {event['name']} ±{event['delta_minutes']}m]"
    return f"{description}{marker}"
