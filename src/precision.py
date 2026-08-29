"""
Rolling-precision math for anomaly feedback.

Pure functions: no database, no I/O. Used by both the FastAPI
endpoint (over Postgres rows) and the livesite publisher (over
FileStore entries). Same data shape in, same numbers out — the
API path uses (event_id, label) pairs, the livesite path uses
(symbol, timestamp) pairs; both eventually key on the same event.

SINGLE SOURCE OF TRUTH for detector attribution: _DETECTOR_KEYWORDS
and detector_from_description live here and are imported by
src/api/main.py (and any other surface that needs them). When adding
a detector, add its anomaly type here — nowhere else.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional

_DETECTOR_KEYWORDS: dict[str, str] = {
    "zscore_spike": "zscore",
    "isolation_forest_outlier": "iforest",
    "correlation_break": "correlation",
    "joint_mahalanobis": "joint",
}


def detector_from_description(description: Optional[str]) -> str:
    if not description:
        return "unknown"
    for keyword, label in _DETECTOR_KEYWORDS.items():
        if keyword in description:
            return label
    return "unknown"


def compute_precision(
    labels: Iterable[dict],
    window_days: int = 30,
    now: Optional[datetime] = None,
) -> dict:
    """
    Aggregate feedback entries into a rolling-precision response.

    `labels` is an iterable of dicts that each carry at least
    `noted_at`, `label`, and `description` (for detector attribution).
    Multiple labels for the same anomaly_event_id are de-duplicated
    to the most recent; the rolling window is applied to `noted_at`.

    Returns a dict matching the API's AnomalyPrecisionResponse shape
    (window_days, totals, overall_precision, by_detector list).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - window_days * 86400

    # De-duplicate per event_id, keeping the most recent.
    latest: dict[str, dict] = {}
    for entry in labels:
        event_key = str(entry.get("anomaly_event_id")
                        or f"{entry.get('symbol')}@{entry.get('timestamp')}")
        noted_raw = entry.get("noted_at")
        if not noted_raw:
            continue
        try:
            noted_dt = datetime.fromisoformat(str(noted_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if noted_dt.timestamp() < cutoff:
            continue
        if event_key not in latest or noted_dt > datetime.fromisoformat(
            str(latest[event_key]["noted_at"]).replace("Z", "+00:00")
        ):
            latest[event_key] = entry

    by_detector: dict[str, Counter] = defaultdict(Counter)
    total_confirmed = 0
    total_false_positive = 0
    for entry in latest.values():
        det = detector_from_description(entry.get("description", ""))
        by_detector[det][entry["label"]] += 1
        if entry["label"] == "confirmed":
            total_confirmed += 1
        elif entry["label"] == "false_positive":
            total_false_positive += 1

    total_labeled = total_confirmed + total_false_positive
    overall = (
        round(total_confirmed / total_labeled, 4) if total_labeled else None
    )

    def _p(counter: Counter) -> Optional[float]:
        labeled = counter["confirmed"] + counter["false_positive"]
        if not labeled:
            return None
        return round(counter["confirmed"] / labeled, 4)

    return {
        "window_days": window_days,
        "total_labeled": total_labeled,
        "total_confirmed": total_confirmed,
        "total_false_positive": total_false_positive,
        "overall_precision": overall,
        "by_detector": [
            {
                "detector": d,
                "labeled": c["confirmed"] + c["false_positive"],
                "confirmed": c["confirmed"],
                "false_positive": c["false_positive"],
                "precision": _p(c),
            }
            for d, c in sorted(by_detector.items())
        ],
    }
