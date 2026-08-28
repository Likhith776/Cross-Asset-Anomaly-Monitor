"""CoinbaseProvider: mapping, success, failure. No real network."""

import pytest

from src.producers.coinbase_provider import COINBASE_SYMBOL_MAP, CoinbaseProvider


def test_symbol_map_covers_btc():
    assert "BTC-USD" in COINBASE_SYMBOL_MAP


def test_parses_spot_price(monkeypatch):
    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"data": {"amount": "78635.355"}}

    captured = []

    def fake_get(url, **kw):
        captured.append(url)
        return Resp()

    monkeypatch.setattr("requests.get", fake_get)
    quotes = CoinbaseProvider(["BTC-USD"]).fetch_all()

    assert len(quotes) == 1
    q = quotes[0]
    assert q["symbol"] == "BTC-USD"
    assert q["price"] == 78635.355
    assert q["source"] == "coinbase"
    assert "BTC-USD" in captured[0]


def test_non_200_returns_empty(monkeypatch):
    class Resp:
        status_code = 503
        text = ""
        json = None

    monkeypatch.setattr("requests.get", lambda *a, **kw: Resp())
    assert CoinbaseProvider(["BTC-USD"]).fetch_all() == []


def test_unmapped_symbols_skipped():
    provider = CoinbaseProvider(["FAKE-X", "^GSPC"])
    assert provider.symbols == []
