"""Data provider unit tests — network-free via stubs."""

from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.producers.data_provider import (
    FinnhubProvider,
    MarketDataProvider,
    YFinanceProvider,
    safe_pct_change,
)


# --- safe_pct_change ---------------------------------------------------------

def test_safe_pct_change_basic():
    assert safe_pct_change(105.0, 100.0) == 0.05
    assert safe_pct_change(95.0, 100.0) == -0.05


def test_safe_pct_change_invalid_inputs():
    assert safe_pct_change(None, 100.0) is None
    assert safe_pct_change(100.0, None) is None
    assert safe_pct_change(100.0, 0.0) is None
    assert safe_pct_change(float("nan"), 100.0) is None
    assert safe_pct_change(250.0, 100.0) is None  # >100% move = bad data


# --- FinnhubProvider (stubbed HTTP) ------------------------------------------

class _FakeResp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, prices):
        self.prices = prices
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        return _FakeResp({"c": self.prices[params["symbol"]]})


def _finnhub_with_prices(prices):
    p = FinnhubProvider(
        api_key="test",
        symbol_map={"BTC-USD": "BINANCE:BTCUSDT", "EURUSD=X": "OANDA:EUR_USD"},
    )
    p._session = _FakeSession(prices)
    return p


def test_finnhub_first_quote_has_no_returns():
    p = _finnhub_with_prices({"BINANCE:BTCUSDT": 100.0, "OANDA:EUR_USD": 1.10})
    quotes = p.fetch()
    assert quotes["BTC-USD"]["price"] == 100.0
    assert quotes["BTC-USD"]["return_1m"] is None


def test_finnhub_return_from_price_memory():
    p = _finnhub_with_prices({"BINANCE:BTCUSDT": 100.0, "OANDA:EUR_USD": 1.10})
    p.fetch()
    p._session = _finnhub_with_prices({"BINANCE:BTCUSDT": 105.0, "OANDA:EUR_USD": 1.10})._session
    quotes = p.fetch()
    assert quotes["BTC-USD"]["return_1m"] == pytest.approx(0.05)


def test_finnhub_dead_symbol_routing():
    p = _finnhub_with_prices({})
    for _ in range(3):
        p._register_failure("EURUSD=X", "test failure")
    assert "EURUSD=X" not in p.symbols
    assert "BTC-USD" in p.symbols


# --- YFinanceProvider (stubbed bars / live price) -----------------------------

def _bars_frame(closes, volumes=None, start=None):
    start = start or datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)
    idx = pd.DatetimeIndex(
        [start + timedelta(minutes=i) for i in range(len(closes))], name="Datetime"
    )
    vols = volumes if volumes is not None else [100] * len(closes)
    return pd.DataFrame({"Close": closes, "Volume": vols}, index=idx)


def test_yfinance_bar_path_returns_and_volume(monkeypatch):
    p = YFinanceProvider()
    monkeypatch.setattr(
        p, "_bars", lambda s: _bars_frame([100.0, 100.5, 101.0, 101.5, 101.2, 101.0])
    )
    monkeypatch.setattr(p, "_live_price", lambda s: None)

    quotes = p.fetch(["BTC-USD"])
    q = quotes["BTC-USD"]
    assert q["price"] == 101.0                     # last bar close
    assert q["return_1m"] == pytest.approx(round(101.0 / 101.2 - 1, 6))   # vs iloc[-2]
    assert q["return_5m"] == pytest.approx(round(101.0 / 100.0 - 1, 6))   # vs iloc[-6]
    assert q["volume"] == 100


def test_yfinance_dedupes_unchanged_symbol(monkeypatch):
    p = YFinanceProvider()
    bars = _bars_frame([100.0, 100.5, 101.0])
    monkeypatch.setattr(p, "_bars", lambda s: bars)
    monkeypatch.setattr(p, "_live_price", lambda s: None)

    assert "BTC-USD" in p.fetch(["BTC-USD"])
    assert "BTC-USD" not in p.fetch(["BTC-USD"])   # unchanged -> deduped

    # A new bar (timestamp advanced) republishes even at the same close
    bars2 = _bars_frame([100.0, 100.5, 101.0, 101.0])
    monkeypatch.setattr(p, "_bars", lambda s: bars2)
    assert "BTC-USD" in p.fetch(["BTC-USD"])


def test_yfinance_live_print_uses_memory_returns(monkeypatch):
    p = YFinanceProvider()
    closes = [100.0, 100.5, 101.0, 101.5, 101.2, 101.0]
    monkeypatch.setattr(p, "_bars", lambda s: _bars_frame(closes))

    live_prices = iter([102.5, 102.6])  # intrabar prints; must change to pass dedupe
    monkeypatch.setattr(p, "_live_price", lambda s: next(live_prices))

    now = datetime.now(timezone.utc)

    first = p.fetch(["BTC-USD"])
    assert first["BTC-USD"]["price"] == 102.5
    # No sample ~60s old yet -> returns unavailable
    assert first["BTC-USD"]["return_1m"] is None

    # Age the memory sample past the 60s target (minus 15s tolerance)
    mem = p._memory["BTC-USD"]
    mem.clear()
    mem.append((now - timedelta(seconds=65), 101.0))

    second = p.fetch(["BTC-USD"])
    assert second["BTC-USD"]["return_1m"] == pytest.approx(round(102.6 / 101.0 - 1, 6))


def test_memory_return_picks_closest_aged_sample():
    now = datetime.now(timezone.utc)
    mem = deque([
        (now - timedelta(seconds=320), 99.0),   # older than the 5m target
        (now - timedelta(seconds=200), 100.0),
        (now - timedelta(seconds=120), 101.0),
        (now - timedelta(seconds=40), 102.0),   # too recent for a 1m return
        (now - timedelta(seconds=10), 103.0),
    ])
    # 1m target: newest sample at least 45s old -> 101.0
    r1 = YFinanceProvider._memory_return(mem, 104.0, now, 60)
    assert r1 == pytest.approx(round(104.0 / 101.0 - 1, 6))
    # 5m target: newest sample at least 285s old -> 99.0
    r5 = YFinanceProvider._memory_return(mem, 104.0, now, 300)
    assert r5 == pytest.approx(round(104.0 / 99.0 - 1, 6))


def test_facade_falls_back_to_yfinance_when_finnhub_fails(monkeypatch):
    provider = MarketDataProvider(["^GSPC", "BTC-USD"])

    class _BrokenFinnhub:
        symbols = ["BTC-USD"]

        def fetch(self):
            raise RuntimeError("network down")

    provider.finnhub = _BrokenFinnhub()
    monkeypatch.setattr(
        provider.yfinance,
        "fetch",
        lambda symbols: {
            s: {
                "symbol": s, "price": 1.0, "return_1m": None,
                "return_5m": None, "volume": None, "source": "yfinance",
            }
            for s in symbols
        },
    )

    quotes = provider.fetch_all()
    assert len(quotes) == 2
    assert all(q["source"] == "yfinance" for q in quotes)
