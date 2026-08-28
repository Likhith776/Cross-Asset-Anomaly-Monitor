"""CompositeProvider: primary wins, secondary retries only for gaps.

The constructor change: it now takes an explicit `symbols` list so
coverage tracking is unambiguous (previously it relied on the
secondary's optional `symbols` attribute, which the real YFinance
facade doesn't expose).
"""

import pytest

from src.producers.composite_provider import CompositeProvider


class Stub:
    def __init__(self, name, symbols, cycles):
        self.name = name
        self.symbols = list(symbols)
        self.cycles = list(cycles)

    def fetch_all(self):
        return self.cycles.pop(0) if self.cycles else []


def q(sym, price=100.0):
    return {"symbol": sym, "price": price}


def test_primary_wins_over_secondary():
    primary = Stub("p", ["BTC-USD"], [[q("BTC-USD", 70000)]])
    secondary = Stub("s", ["BTC-USD"], [[q("BTC-USD", 1.0)]])

    out = CompositeProvider(
        symbols=["BTC-USD"], primaries=[primary], secondary=secondary,
    ).fetch_all()

    assert len(out) == 1
    assert out[0]["price"] == 70000


def test_secondary_fills_symbols_primary_missed():
    primary = Stub("p", ["BTC-USD"], [[q("BTC-USD", 70000)]])
    secondary = Stub("s", ["BTC-USD", "^GSPC"], [[q("^GSPC", 5600)]])

    out = CompositeProvider(
        symbols=["BTC-USD", "^GSPC"],
        primaries=[primary], secondary=secondary, attempts=1,
    ).fetch_all()

    syms = {x["symbol"]: x["price"] for x in out}
    assert syms == {"BTC-USD": 70000, "^GSPC": 5600}


def test_secondary_retried_until_coverage_complete(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    primary = Stub("p", ["BTC-USD"], [[], []])
    secondary = Stub("s", ["BTC-USD"], [[], [q("BTC-USD", 71000)]])

    out = CompositeProvider(
        symbols=["BTC-USD"],
        primaries=[primary], secondary=secondary, attempts=2, retry_wait=0,
    ).fetch_all()

    assert {x["symbol"] for x in out} == {"BTC-USD"}
    assert any(x["price"] == 71000 for x in out)


def test_gives_up_after_attempts_but_keeps_primaries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    primary = Stub("p", ["BTC-USD"], [[q("BTC-USD", 70000)], [q("BTC-USD", 70001)]])
    secondary = Stub("s", ["BTC-USD", "^GSPC"], [[], []])

    out = CompositeProvider(
        symbols=["BTC-USD", "^GSPC"],
        primaries=[primary], secondary=secondary, attempts=2, retry_wait=0,
    ).fetch_all()

    assert [x["symbol"] for x in out] == ["BTC-USD"]


def test_primary_exception_does_not_break_cycle():
    class Exploding:
        symbols = ["BTC-USD"]

        def fetch_all(self):
            raise RuntimeError("network down")

    secondary = Stub("s", ["BTC-USD"], [[q("BTC-USD", 70500)]])
    out = CompositeProvider(
        symbols=["BTC-USD"],
        primaries=[Exploding()], secondary=secondary, attempts=1,
    ).fetch_all()

    assert out and out[0]["price"] == 70500


def test_extra_symbols_outside_universe_are_ignored():
    primary = Stub("p", ["BTC-USD"], [[q("BTC-USD", 70000)]])
    secondary = Stub("s", ["^GSPC"], [[q("^GSPC", 5600)]])

    out = CompositeProvider(
        symbols=["BTC-USD"],   # ^GSPC not in our universe today
        primaries=[primary], secondary=secondary, attempts=1,
    ).fetch_all()

    assert [x["symbol"] for x in out] == ["BTC-USD"]
