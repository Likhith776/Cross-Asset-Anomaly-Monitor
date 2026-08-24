"""Replay historical price panels through the DetectionPipeline and score
the resulting alerts against planted-event ground truth."""

from datetime import datetime

import numpy as np
import pandas as pd

from src.detection.pipeline import DetectionPipeline


def replay_panel(
    panel: pd.DataFrame,
    pipe: DetectionPipeline,
    *,
    warmup_ticks: int = 150,
    use_warm_start: bool = True,
) -> list[dict]:
    """
    Feed every cell of the panel (rows in time order, then each symbol)
    through `pipe` exactly like live ticks arrive. Returns alert dicts
    annotated with `_tick` (row index in the panel) and `_sym`.

    The first `warmup_ticks` rows prime detector state — via
    pipeline.warm_start when enabled, otherwise by plain replay — and are
    never counted as alerts.
    """
    if len(panel) <= warmup_ticks:
        raise ValueError(
            f"panel has {len(panel)} rows; needs more than warmup_ticks={warmup_ticks}"
        )

    warm = panel.iloc[:warmup_ticks]
    if use_warm_start:
        prices_by_symbol: dict[str, list[tuple[datetime, float]]] = {}
        for sym in panel.columns:
            prices_by_symbol[sym] = [
                (ts.to_pydatetime(), float(price))
                for ts, price in warm[sym].items()
            ]
        pipe.warm_start(prices_by_symbol=prices_by_symbol)
        eval_rows = range(warmup_ticks, len(panel))
    else:
        eval_rows = range(0, len(panel))

    alerts: list[dict] = []
    for i in eval_rows:
        row = panel.iloc[i]
        ts = panel.index[i].to_pydatetime()
        for sym in panel.columns:
            price = row[sym]
            if pd.isna(price):
                continue
            for found in pipe.detect(sym, float(price), ts):
                found["_tick"] = i
                found["_sym"] = sym
                alerts.append(found)
    return alerts


def _alert_matches_event(alert: dict, event, target: str) -> bool:
    """True if an alert falls inside the event window (+ small lag margin)."""
    lag_margin = 5
    in_window = event.start <= alert["_tick"] <= event.end + lag_margin
    if not in_window:
        return False
    # Point-detector alerts carry the processed symbol; correlation-break
    # alerts fire on either member of the pair being checked.
    if alert["asset"] == target:
        return True
    pair = alert.get("metadata", {}).get("pair", "")
    return target in pair.split("/")


def score_trial(alerts: list[dict], events: list, target: str) -> dict[str, dict]:
    """
    Aggregate one trial's alerts per detector type.

    An alert "matches" an event when it lands inside the event window;
    each event counts as caught at most once per detector type (the first
    matching alert), so duplicate alarms don't inflate recall.

    Returns {detector_type: {"alerts", "matched_events", "precision",
    "recall", "median_lag"}} where precision is None when the detector
    stayed silent.
    """
    out: dict[str, dict] = {}
    all_types = sorted({a["type"] for a in alerts})
    for t in all_types:
        det_alerts = [a for a in alerts if a["type"] == t]
        matched_events = 0
        true_positive_alerts = 0
        lags = []
        for ev in events:
            hits = [a for a in det_alerts if _alert_matches_event(a, ev, target)]
            if hits:
                matched_events += 1
                true_positive_alerts += 1  # count one alarm per event
                lags.append(min(a["_tick"] for a in hits) - ev.start)
        # Alarms beyond one-per-event are false alarms on clean data.
        false_alarms = max(len(det_alerts) - true_positive_alerts, 0)
        precision = (
            round(true_positive_alerts / (true_positive_alerts + false_alarms), 3)
            if det_alerts
            else None
        )
        out[t] = {
            "alerts": len(det_alerts),
            "matched_events": matched_events,
            "precision": precision,
            "recall": round(matched_events / len(events), 3) if events else None,
            "median_lag": int(np.median(lags)) if lags else None,
        }
    return out
