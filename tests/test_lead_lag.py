"""Lead-lag annotation: cross-correlation, leader/lag identification,
threshold/noise behavior, description marker, pipeline accessor.

Offline and deterministic — synthetic series with known lag and
correlation, seeded noise.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.detection.lead_lag import (
    augment_description,
    extract_lead_lag,
    lead_lag_for,
)
from src.detection.pipeline import DetectionPipeline


def _lagged_pair(n=80, lag=2, rho=0.9, sigma=0.001, seed=7):
    """
    returns_by_symbol where SYMBOL's returns are PARTNER's returns
    shifted by exactly `lag` ticks (with noise), so PARTNER led.
    """
    rng = np.random.default_rng(seed)
    partner_returns = list(rng.normal(0, sigma, n))
    symbol_returns = [
        rho * partner_returns[i - lag] + (1 - rho) * float(rng.normal(0, sigma))
        if i >= lag else float(rng.normal(0, sigma))
        for i in range(n)
    ]
    return {"PARTNER": partner_returns, "SYMBOL": symbol_returns}


PAIRS = [("SYMBOL", "PARTNER")]


# ---------------------------------------------------------------------------
# Core identification

def test_partner_led_identified_with_correct_lag_and_correlation():
    returns = _lagged_pair(lag=2, rho=0.95)
    result = lead_lag_for("SYMBOL", returns, pairs=PAIRS)

    assert result is not None
    assert result["leader"] == "PARTNER"   # partner moved first
    assert result["lag_ticks"] == 2
    assert result["correlation"] > 0.9


def test_symbol_led_identified_when_symbol_moves_first():
    returns = _lagged_pair(lag=2, rho=0.95)
    # Reverse the roles: PARTNER now trails SYMBOL by 2 ticks.
    flipped = {
        "SYMBOL": returns["PARTNER"],
        "PARTNER": returns["SYMBOL"],
    }
    result = lead_lag_for("SYMBOL", flipped, pairs=PAIRS)

    assert result is not None
    assert result["leader"] == "SYMBOL"    # the anomaly symbol moved first
    assert result["lag_ticks"] == 2


def test_uncorrelated_series_returns_none():
    rng = np.random.default_rng(3)
    returns = {
        "SYMBOL": list(rng.normal(0, 0.001, 80)),
        "PARTNER": list(rng.normal(0, 0.001, 80)),
    }
    assert lead_lag_for("SYMBOL", returns, pairs=PAIRS) is None


def test_below_minimum_correlation_returns_none():
    rng = np.random.default_rng(4)
    partner = list(rng.normal(0, 0.001, 80))
    # ~0.2 correlated echo: visible but below the 0.5 reporting floor.
    symbol = [0.2 * p + float(rng.normal(0, 0.001 * 0.95)) for p in partner]
    returns = {"SYMBOL": symbol, "PARTNER": partner}

    assert lead_lag_for("SYMBOL", returns, pairs=PAIRS) is None


def test_zero_lag_only_correlation_returns_none():
    """Contemporaneous correlation is not lead-lag: an exact copy of an
    i.i.d. series correlates perfectly at lag 0 and ~nowhere else."""
    rng = np.random.default_rng(9)
    r = list(rng.normal(0, 0.001, 80))
    returns = {"SYMBOL": r, "PARTNER": list(r)}   # exact copy
    assert lead_lag_for("SYMBOL", returns, pairs=PAIRS) is None


def test_insufficient_samples_returns_none():
    returns = _lagged_pair(n=80, lag=2)
    # Truncate below min_samples + max_lag.
    returns = {s: r[:20] for s, r in returns.items()}
    assert lead_lag_for("SYMBOL", returns, pairs=PAIRS) is None


def test_partner_outside_universe_pairs_is_ignored():
    returns = _lagged_pair(lag=2)
    returns["STRANGER"] = list(returns["PARTNER"])  # strong but unpaired

    # Without the pair configured: no signal.
    assert lead_lag_for("SYMBOL", returns, pairs=[("SYMBOL", "UNRELATED")]) is None
    # With the pair configured: found.
    assert lead_lag_for("SYMBOL", returns, pairs=PAIRS) is not None


def test_strongest_partner_wins_when_several_are_configured():
    returns = _lagged_pair(lag=2, rho=0.95)
    weak = [0.3 * p for p in returns["PARTNER"]]
    returns["WEAK"] = [0.9 * p for p in returns["PARTNER"]]  # same echo, weaker
    returns["SYMBOL"] = [
        0.95 * returns["PARTNER"][i - 2] + float(np.random.default_rng(1).normal(0, 0.001))
        if i >= 2 else 0.0
        for i in range(len(returns["PARTNER"]))
    ]

    pairs = [("SYMBOL", "PARTNER"), ("SYMBOL", "WEAK")]
    result = lead_lag_for("SYMBOL", returns, pairs=pairs)
    assert result["leader"] == "PARTNER"   # stronger |correlation| wins


# ---------------------------------------------------------------------------
# Description marker

def test_augment_description_appends_parseable_marker():
    result = {"leader": "GC=F", "lag_ticks": 2, "correlation": 0.71}
    out = augment_description("Tick-level zscore_spike — high severity", result)
    assert out.startswith("Tick-level zscore_spike — high severity")
    assert "[lead-lag: led by GC=F (2 ticks, r=0.71)]" in out
    assert extract_lead_lag(out) == result


def test_extract_lead_lag_returns_none_without_marker():
    assert extract_lead_lag("plain description") is None
    assert extract_lead_lag(None) is None


# ---------------------------------------------------------------------------
# Pipeline accessor

def test_pipeline_recent_returns_computed_from_windows():
    pipe = DetectionPipeline()
    ts = datetime(2030, 1, 1, tzinfo=timezone.utc)
    prices = [100.0, 100.1, 100.3, 100.2]
    for i, p in enumerate(prices):
        pipe.detect("TEST-X", p, ts + timedelta(hours=i))

    rets = pipe.recent_returns()["TEST-X"]
    expected = [prices[i + 1] / prices[i] - 1 for i in range(3)]
    assert rets == pytest.approx(expected)


def test_pipeline_recent_returns_skips_zero_prices():
    pipe = DetectionPipeline()
    ts = datetime(2030, 1, 1, tzinfo=timezone.utc)
    for i, p in enumerate([100.0, 0.0, 101.0]):
        pipe.detect("TEST-X", p, ts + timedelta(hours=i))

    rets = pipe.recent_returns()["TEST-X"]
    assert all(np.isfinite(r) for r in rets)


# ---------------------------------------------------------------------------
# Slim wiring: the annotation reaches the persisted description

def test_slim_tick_path_appends_lead_lag_marker(monkeypatch):
    import src.slim as slim_module
    from src.consumers.feature_consumer import SymbolWindows
    from src.slim import SlimApp

    monkeypatch.setattr(
        slim_module, "lead_lag_for",
        lambda symbol, returns, **kw: {
            "leader": "PARTNER", "partner": "PARTNER",
            "lag_ticks": 2, "correlation": 0.71,
        },
    )

    app = SlimApp.__new__(SlimApp)
    app.symbols = ["TEST-X"]
    app.stop_event = __import__("threading").Event()
    app.message_count = 0
    app.windows = SymbolWindows(app.symbols, maxlen=200)
    app.pipeline = DetectionPipeline()
    app.writer = type("W", (), {
        "write_feature": lambda self, rec: True,
        "write_correlation_snapshot": lambda self, ts, pairs: True,
    })()
    app.persisted = []
    app._last_event = lambda symbol: (None, None)
    app._persist_anomaly = lambda params: app.persisted.append(params)

    rng = np.random.default_rng(7)
    ts0 = datetime(2030, 1, 1, tzinfo=timezone.utc)
    # Quiet ramp (deterministic spike per the test_slim pattern) ...
    for i in range(30):
        app.process_tick({
            "symbol": "TEST-X", "price": 100.0 + i * 0.01,
            "timestamp": (ts0 + timedelta(minutes=i)).isoformat(),
            "return_1m": None, "return_5m": None, "volume": 1,
        })
    # ... then a hard spike that deterministically fires.
    app.process_tick({
        "symbol": "TEST-X", "price": 130.0,
        "timestamp": (ts0 + timedelta(minutes=30)).isoformat(),
        "return_1m": None, "return_5m": None, "volume": 1,
    })

    assert app.persisted, "expected the spike to be detected"
    description = app.persisted[-1][6]
    assert "[lead-lag: led by PARTNER (2 ticks, r=0.71)]" in description
