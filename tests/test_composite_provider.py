"""CompositeProvider: primary wins, secondary retries only for gaps."""

import pytest

from src.producers.composite_provider import CompositeProvider


class Stub:
    def __init__(self, symbols, cycles):
        self.symbols = list(symbols)
        self.cycles = list(cycles)

    def fetch_all(self):
        return self.cycles.pop(0) if self.cycles else []


def q(sym, price=100.0):
    return {"symbol": sym, "price": price}


def test_primary_wins_over_secondary():
    primary = Stub(["BTC-USD"], [[q("BTC-USD", 70000)]])
    secondary = Stub(["BTC-USD"], [[q("BTC-USD", 1.0)]])

    out = CompositeProvider([primary], secondary).fetch_all()

    assert len(out) == 1
    assert out[0]["price"] == 70000  # primary value kept


def test_secondary_fills_symbols_primary_missed():
    primary = Stub(["BTC-USD"], [[q("BTC-USD", 70000)]])
    secondary = Stub(["BTC-USD", "^GSPC"], [[q("^GSPC", 5600)]])

    out = CompositeProvider(
        [primary], secondary, attempts=1
    ).fetch_all()

    syms = {x["symbol"]: x["price"] for x in out}
    assert syms == {"BTC-USD": 70000, "^GSPC": 5600}


def test_secondary_retried_until_coverage_complete(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    primary = Stub(["BTC-USD"], [[], []])
    secondary = Stub(
        ["BTC-USD"],
        [[], [q("BTC-USD", 71000)]],  # first pass blocked, second succeeds
    )

    out = CompositeProvider([primary], secondary, attempts=2, retry_wait=0).fetch_all()

    assert {x["symbol"] for x in out} == {"BTC-USD"}
    assert any(x["price"] == 71000 for x in out)


def test_gives_up_after_attempts_but_keeps_primaries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    primary = Stub(["BTC-USD"], [[q("BTC-USD", 70000)], [q("BTC-USD", 70001)]])
    secondary = Stub(["BTC-USD", "^GSPC"], [[], []])  # always blocked

    out = CompositeProvider([primary], secondary, attempts=2, retry_wait=0).fetch_all()

    assert [x["symbol"] for x in out] == ["BTC-USD"]


def test_primary_exception_does_not_break_cycle():
    class Exploding:
        symbols = ["BTC-USD"]

        def fetch_all(self):
            raise RuntimeError("network down")

    secondary = Stub(["BTC-USD"], [[q("BTC-USD", 70500)]])
    out = CompositeProvider([Exploding()], secondary, attempts=1).fetch_all()

    assert out and out[0]["price"] == 70500
