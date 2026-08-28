"""OpenExchangeRatesProvider parsing; mapping coverage. Offline."""

import json

import pytest

from src.producers import openexchangerates_provider as oxr
from src.producers.openexchangerates_provider import OpenExchangeRatesProvider


def test_unmapped_symbols_skipped():
    p = OpenExchangeRatesProvider(["FAKE", "EURUSD=X"])
    assert p.symbols == ["EURUSD=X"]


def test_quotes_eurusd_from_payload(monkeypatch):
    payload = {
        "result": "success",
        "time_last_update_unix": 1787875351,
        "rates": {"EUR": 0.858266, "GBP": 0.735818},
    }
    class FakeResp:
        status_code = 200
        text = json.dumps(payload)

        def json(self):
            return payload

    monkeypatch.setattr("requests.get", lambda *a, **kw: FakeResp())
    quotes = OpenExchangeRatesProvider(["EURUSD=X"]).fetch_all()

    assert len(quotes) == 1
    q = quotes[0]
    assert q["symbol"] == "EURUSD=X"
    assert q["price"] == pytest.approx(1.0 / 0.858266, rel=1e-6)
    assert q["source"] == "openexchangerates"
    assert "2026" in q["timestamp"]


def test_http_error_yields_empty(monkeypatch):
    class FakeResp:
        status_code = 500
        text = ""

        def json(self):
            return {}

    monkeypatch.setattr("requests.get", lambda *a, **kw: FakeResp())
    assert OpenExchangeRatesProvider(["EURUSD=X"]).fetch_all() == []


def test_non_success_payload_yields_empty(monkeypatch):
    payload = {"result": "error", "rates": {"EUR": 0.85}}
    class FakeResp:
        status_code = 200
        text = json.dumps(payload)
        def json(self):
            return payload
    monkeypatch.setattr("requests.get", lambda *a, **kw: FakeResp())
    assert OpenExchangeRatesProvider(["EURUSD=X"]).fetch_all() == []


def test_missing_rate_skipped_silently(monkeypatch):
    payload = {"result": "success", "rates": {"GBP": 0.7}}
    class FakeResp:
        status_code = 200
        text = json.dumps(payload)
        def json(self):
            return payload
    monkeypatch.setattr("requests.get", lambda *a, **kw: FakeResp())
    assert OpenExchangeRatesProvider(["EURUSD=X"]).fetch_all() == []