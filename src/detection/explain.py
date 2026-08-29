"""
Optional LLM root-cause explanations for high-severity anomalies.

A single Gemini API call over the concurrent cross-asset feature
snapshot (price moves, vols, regimes, correlation state, lead-lag
hint, macro-calendar match) produces a 1-2 sentence factual
explanation stored alongside the anomaly record.

Hard rules enforced here:
- ENTIRELY OPTIONAL: with GEMINI_API_KEY unset the feature is off and
  every surface behaves identically (explanation stored as null).
- NEVER a dependency of the anomaly record: any failure — missing key,
  exhausted budget, HTTP error, timeout, malformed response — returns
  None and the anomaly is persisted exactly as it would be without
  this module.
- FREE-TIER DISCIPLINE: a process-wide sliding-window budget (hourly +
  daily caps) is checked BEFORE any network call and consumed even for
  failed calls, so retries can never turn into a quota storm. The
  severity gate (high/critical only) plus these caps keep usage far
  below the Gemini free-tier limits even for the unattended scheduled
  paths. The limiter is in-process; with multiple services explaining
  independently the budgets multiply (documented in the README), which
  is why the per-process caps are set far below the tier's RPD.

The prompt is strictly data-driven: the model receives the anomaly and
market snapshot as JSON and is instructed not to speculate about news
or causes that are not present in the data.

Model note: GEMINI_MODEL below was pinned after checking
https://ai.google.dev/gemini-api/docs/pricing — gemini-2.5-flash is
listed "Free of charge" on the Gemini API free tier. Model names and
tiers change: re-verify that page when updating this constant.
"""

import json
import logging
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Pinned free-tier-eligible model (see pricing note above). Update here,
# nowhere else.
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)
GEMINI_TIMEOUT_SECONDS = 12

# Severity gate: 0.7 is the "high" boundary in _classify_severity, so
# only high/critical anomalies reach the LLM. Lower-severity events are
# routine; explaining them would burn free-tier quota without adding
# review value. Together with the budgets below this keeps usage far
# from free-tier limits even on unattended scheduled paths.
EXPLAIN_MIN_SCORE = 0.7

# Sliding-window budget per process. Worst realistic case is two
# independent processes explaining concurrently (tick consumer +
# scheduler): 2 x 30/day = 60/day, still far under the free tier's
# ~250 RPD for this model — and the severity gate means a burst of
# 6 calls/hour essentially never happens in practice.
MAX_CALLS_PER_HOUR = 6
MAX_CALLS_PER_DAY = 30
MAX_EXPLANATION_CHARS = 600


class _Budget:
    """Sliding-window call budget (hourly + daily), process-wide."""

    def __init__(self, max_per_hour: int = MAX_CALLS_PER_HOUR,
                 max_per_day: int = MAX_CALLS_PER_DAY):
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day
        self._hour: deque = deque()
        self._day: deque = deque()

    def acquire(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        while self._hour and self._hour[0] <= now - timedelta(hours=1):
            self._hour.popleft()
        while self._day and self._day[0] <= now - timedelta(days=1):
            self._day.popleft()
        if len(self._hour) >= self.max_per_hour or len(self._day) >= self.max_per_day:
            return False
        self._hour.append(now)
        self._day.append(now)
        return True


_budget = _Budget()


def is_enabled() -> bool:
    """True only when GEMINI_API_KEY is set to a non-empty value."""
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def build_snapshot_from_pipeline(
    pipeline,
    lead_lag: Optional[dict] = None,
    macro_event: Optional[dict] = None,
) -> dict:
    """
    Cross-asset feature snapshot from the live pipeline state: latest
    price, last five per-tick moves (%), running vol, and regime per
    symbol, plus the latest correlation per monitored pair.
    """
    symbols = []
    returns = pipeline.recent_returns()
    tracker = pipeline.regime_tracker
    for detector in pipeline.detectors:
        if not hasattr(detector, "regime_by_asset"):
            continue
        for symbol, prices in detector._price_windows.items():
            if any(s["symbol"] == symbol for s in symbols):
                continue
            moves = [round(r * 100, 3) for r in returns.get(symbol, [])][-5:]
            symbols.append({
                "symbol": symbol,
                "price": round(prices[-1], 4) if prices else None,
                "last_moves_pct": moves,
                "vol": round(tracker.current_vol(symbol), 6)
                        if tracker.current_vol(symbol) is not None else None,
                "regime": tracker.current_regime(symbol),
            })

    correlations = {}
    for detector in pipeline.detectors:
        history = getattr(detector, "_corr_history", None)
        if not history:
            continue
        for (a, b), values in history.items():
            if values:
                correlations[f"{a}/{b}"] = round(float(values[-1]), 4)

    return {
        "symbols": symbols,
        "correlations": correlations,
        "lead_lag": lead_lag,
        "macro_event": macro_event,
    }


def build_snapshot_from_features(
    features_by_symbol: dict[str, list[dict]],
    pairs: list[tuple[str, str]],
    lead_lag: Optional[dict] = None,
    macro_event: Optional[dict] = None,
) -> dict:
    """
    Same snapshot shape, built from the batch engine's per-symbol
    feature windows (price, return_1m, ewma_vol) instead of live
    pipeline state.
    """
    from src.detection.regime import classify_vol_percentile

    symbols = []
    return_series: dict[str, list[float]] = {}
    for symbol, features in features_by_symbol.items():
        if not features:
            continue
        latest = features[-1]
        returns = [
            float(f["return_1m"]) for f in features
            if f.get("return_1m") is not None
        ]
        return_series[symbol] = returns
        vols = [
            float(f["ewma_vol"]) for f in features
            if f.get("ewma_vol") is not None
        ]
        vol = vols[-1] if vols else None
        regime = (
            classify_vol_percentile(vol, vols[:-1])
            if vol is not None and len(vols) >= 20
            else "unknown"
        )
        symbols.append({
            "symbol": symbol,
            "price": round(float(latest.get("price")), 4)
                     if latest.get("price") is not None else None,
            "last_moves_pct": [round(r * 100, 3) for r in returns[-5:]],
            "vol": vol,
            "regime": regime,
        })

    correlations = {}
    for a, b in pairs:
        ra, rb = return_series.get(a), return_series.get(b)
        if not ra or not rb:
            continue
        n = min(len(ra), len(rb))
        if n < 10:
            continue
        xa, xb = np_last(ra, n), np_last(rb, n)
        corr = _safe_corr(xa, xb)
        if corr is not None:
            correlations[f"{a}/{b}"] = corr

    return {
        "symbols": symbols,
        "correlations": correlations,
        "lead_lag": lead_lag,
        "macro_event": macro_event,
    }


def np_last(values: list[float], n: int) -> list[float]:
    import numpy as np

    arr = np.asarray(values[-n:], dtype=float)
    return arr  # kept as list-like for corrcoef


def _safe_corr(x, y) -> Optional[float]:
    import numpy as np

    arr_x, arr_y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if len(arr_x) != len(arr_y) or len(arr_x) < 2:
        return None
    std_x, std_y = float(np.std(arr_x)), float(np.std(arr_y))
    if std_x < 1e-12 or std_y < 1e-12:
        return None
    return round(float(np.corrcoef(arr_x, arr_y)[0, 1]), 4)


# ---------------------------------------------------------------------------
# Prompt + Gemini call
# ---------------------------------------------------------------------------

def _build_prompt(anomaly: dict, snapshot: dict) -> str:
    compact_anomaly = {
        k: anomaly.get(k)
        for k in ("symbol", "type", "score", "severity", "price", "description")
        if anomaly.get(k) is not None
    }
    return (
        "You are a markets analyst reviewing an automated anomaly "
        "detection alert. Using ONLY the JSON data below, explain in 1-2 "
        "factual sentences what the data shows about this anomaly and how "
        "the affected asset relates to the others (co-movement, "
        "correlation breaks, lead-lag, scheduled events, volatility "
        "regime). Do NOT speculate about news, rumors, or causes that are "
        "not present in the data. If the data is insufficient to say "
        "anything meaningful, reply with exactly: insufficient data.\n\n"
        "ANOMALY:\n"
        f"{json.dumps(compact_anomaly, default=str)}\n\n"
        "CROSS-ASSET SNAPSHOT:\n"
        f"{json.dumps(snapshot, default=str)}"
    )


def _post_json(prompt: str, api_key: str) -> Optional[str]:
    """Synchronous Gemini REST call; returns the model text or None."""
    import requests

    resp = requests.post(
        GEMINI_API_URL.format(model=GEMINI_MODEL),
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 200},
        },
        timeout=GEMINI_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        logger.info("[EXPLAIN] Gemini returned no candidates")
        return None
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    return text or None


def maybe_explain(
    anomaly: dict,
    snapshot: dict,
    *,
    api_key: Optional[str] = None,
    limiter: Optional[_Budget] = None,
    post: Optional[Callable[[str, str], Optional[str]]] = None,
) -> Optional[str]:
    """
    Gate -> budget -> single Gemini call -> 1-2 sentence explanation.

    Returns None (never raises) when the feature is off, the anomaly is
    below the severity gate, the budget is exhausted, or anything in the
    call path fails. `api_key`/`limiter`/`post` are injectable for tests;
    production callers use explain_from_env().
    """
    score = 0.0
    for key in ("score", "anomaly_score"):
        if anomaly.get(key) is not None:
            try:
                score = float(anomaly[key])
            except (TypeError, ValueError):
                pass
            break

    # Severity gate: high/critical only (0.7 = the "high" boundary in
    # _classify_severity). Documented rationale in the constants above.
    if score < EXPLAIN_MIN_SCORE:
        return None
    if not api_key:
        return None

    budget = limiter or _budget
    if not budget.acquire():
        logger.info("[EXPLAIN] budget exhausted — skipping LLM explanation")
        return None

    prompt = _build_prompt(anomaly, snapshot)
    call = post or _post_json
    try:
        text = call(prompt, api_key)
    except Exception as exc:
        logger.warning("[EXPLAIN] Gemini call failed: %s", exc)
        return None
    if not text:
        return None
    return text[:MAX_EXPLANATION_CHARS].strip() or None


def explain_from_env(anomaly: dict, snapshot: dict) -> Optional[str]:
    """Entry-point wrapper: reads GEMINI_API_KEY from the environment."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    return maybe_explain(anomaly, snapshot, api_key=key or None)
