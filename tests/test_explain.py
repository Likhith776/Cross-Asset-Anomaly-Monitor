"""
Optional LLM explanation module: gating, budget, graceful failure.

Fully offline — the Gemini HTTP call is injected as a fake `post`
function; no real API key is ever read (the autouse conftest fixture
removes GEMINI_API_KEY from the environment).
"""

import pytest

from src.detection.explain import (
    EXPLAIN_MIN_SCORE,
    _Budget,
    maybe_explain,
)

ANOMALY = {
    "symbol": "BTC-USD",
    "type": "isolation_forest_outlier",
    "score": 1.0,
    "severity": "critical",
    "price": 79662.0,
    "description": "Tick-level isolation_forest_outlier — critical severity",
}
SNAPSHOT = {
    "symbols": [
        {"symbol": "BTC-USD", "price": 79662.0,
         "last_moves_pct": [0.4, -0.1, 4.2], "vol": 0.0011,
         "regime": "high"},
        {"symbol": "^GSPC", "price": 7730.0,
         "last_moves_pct": [-0.6, -0.2, 0.1], "vol": 0.0006,
         "regime": "medium"},
    ],
    "correlations": {"GC=F/^GSPC": -0.62},
    "lead_lag": {"leader": "GC=F", "partner": "BTC-USD",
                 "lag_ticks": 2, "correlation": 0.71},
    "macro_event": None,
}


def _fake_post(text="BTC rose 4.2% while SPX fell 0.6% — a broad risk-off move."):
    calls = []

    def post(prompt, api_key):
        calls.append({"prompt": prompt, "api_key": api_key})
        return text

    post.calls = calls
    return post


def _fresh_limiter(max_per_hour=10, max_per_day=100):
    return _Budget(max_per_hour=max_per_hour, max_per_day=max_per_day)


# ---------------------------------------------------------------------------
# Success path

def test_success_attaches_explanation():
    post = _fake_post()
    out = maybe_explain(ANOMALY, SNAPSHOT, api_key="test-key",
                        limiter=_fresh_limiter(), post=post)

    assert out == "BTC rose 4.2% while SPX fell 0.6% — a broad risk-off move."
    assert post.calls[0]["api_key"] == "test-key"
    # Prompt is data-driven: carries the snapshot and the no-speculation rule
    prompt = post.calls[0]["prompt"]
    assert "ANOMALY" in prompt and "CROSS-ASSET SNAPSHOT" in prompt
    assert "Do NOT speculate" in prompt
    assert "BTC-USD" in prompt


def test_explanation_truncated_to_max_chars():
    post = _fake_post(text="x" * 5000)
    out = maybe_explain(ANOMALY, SNAPSHOT, api_key="k",
                        limiter=_fresh_limiter(), post=post)
    from src.detection.explain import MAX_EXPLANATION_CHARS

    assert out is not None and len(out) <= MAX_EXPLANATION_CHARS


# ---------------------------------------------------------------------------
# Graceful failure paths

def test_missing_key_returns_none_without_calling():
    post = _fake_post()
    out = maybe_explain(ANOMALY, SNAPSHOT, api_key=None,
                        limiter=_fresh_limiter(), post=post)
    assert out is None
    assert post.calls == []            # no network attempt at all


def test_api_error_returns_none_without_raising():
    def failing_post(prompt, api_key):
        raise RuntimeError("connection reset")

    out = maybe_explain(ANOMALY, SNAPSHOT, api_key="k",
                        limiter=_fresh_limiter(), post=failing_post)
    assert out is None


def test_empty_response_returns_none():
    post = _fake_post(text="")
    out = maybe_explain(ANOMALY, SNAPSHOT, api_key="k",
                        limiter=_fresh_limiter(), post=post)
    assert out is None


# ---------------------------------------------------------------------------
# Severity gate

def test_low_severity_anomaly_never_triggers_a_call():
    quiet = dict(ANOMALY, score=0.4, severity="low")
    post = _fake_post()
    out = maybe_explain(quiet, SNAPSHOT, api_key="k",
                        limiter=_fresh_limiter(), post=post)

    assert out is None
    assert post.calls == []            # the mock was not invoked at all


def test_score_gate_boundary_at_explain_min_score():
    post = _fake_post()
    at_gate = dict(ANOMALY, score=EXPLAIN_MIN_SCORE)      # 0.7 → explains
    below = dict(ANOMALY, score=EXPLAIN_MIN_SCORE - 0.01)  # 0.69 → silent

    assert maybe_explain(at_gate, SNAPSHOT, api_key="k",
                         limiter=_fresh_limiter(), post=post) is not None
    post.calls.clear()
    assert maybe_explain(below, SNAPSHOT, api_key="k",
                         limiter=_fresh_limiter(), post=post) is None
    assert post.calls == []


# ---------------------------------------------------------------------------
# Budget

def test_budget_blocks_calls_once_exhausted():
    post = _fake_post()
    limiter = _fresh_limiter(max_per_hour=2, max_per_day=100)

    assert maybe_explain(ANOMALY, SNAPSHOT, api_key="k",
                         limiter=limiter, post=post) is not None
    assert maybe_explain(ANOMALY, SNAPSHOT, api_key="k",
                         limiter=limiter, post=post) is not None
    assert maybe_explain(ANOMALY, SNAPSHOT, api_key="k",
                         limiter=limiter, post=post) is None      # blocked
    assert len(post.calls) == 2                    # the third never reached HTTP


def test_hourly_window_slides():
    from datetime import datetime, timedelta, timezone

    limiter = _Budget(max_per_hour=1, max_per_day=100)
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert limiter.acquire(now) is True
    assert limiter.acquire(now + timedelta(minutes=59)) is False
    assert limiter.acquire(now + timedelta(hours=1, seconds=1)) is True


def test_daily_cap_blocks_even_across_hours():
    from datetime import datetime, timedelta, timezone

    limiter = _Budget(max_per_hour=10, max_per_day=2)
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert limiter.acquire(now) is True
    assert limiter.acquire(now + timedelta(hours=2)) is True
    assert limiter.acquire(now + timedelta(hours=4)) is False


# ---------------------------------------------------------------------------
# Prompt hygiene

def test_prompt_includes_lead_lag_and_macro_context():
    snapshot = dict(
        SNAPSHOT,
        lead_lag={"leader": "GC=F", "partner": "BTC-USD",
                  "lag_ticks": 2, "correlation": 0.71},
        macro_event={"name": "FOMC rate decision", "delta_minutes": 7},
    )
    post = _fake_post()
    maybe_explain(ANOMALY, snapshot, api_key="k",
                  limiter=_fresh_limiter(), post=post)

    prompt = post.calls[0]["prompt"]
    assert "GC=F" in prompt and "FOMC rate decision" in prompt
