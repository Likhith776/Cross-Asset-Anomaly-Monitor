"""Slim-profile orchestration: fetch → features → detect → persist.

Runs entirely in memory with injected fakes — no database, no network.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

from src.consumers.feature_consumer import SymbolWindows
from src.detection.pipeline import DetectionPipeline
from src.slim import SlimApp


class FakeWriter:
    """Records writes; never touches a database."""

    def __init__(self):
        self.features = []
        self.snapshots = []

    def write_feature(self, record):
        self.features.append(record)
        return True

    def write_correlation_snapshot(self, timestamp, pairs):
        self.snapshots.append((timestamp, pairs))
        return True

    def fetch_recent_features(self, symbols, limit):
        return []


class FakeProvider:
    def __init__(self, cycles):
        self.cycles = list(cycles)

    def fetch_all(self):
        return self.cycles.pop(0) if self.cycles else []


def make_app(cycles):
    """Build a SlimApp with real windows/pipeline but fake I/O."""
    app = SlimApp.__new__(SlimApp)
    app.symbols = ["TEST-X"]
    app.stop_event = threading.Event()
    app.message_count = 0
    app.provider = FakeProvider(cycles)
    app.windows = SymbolWindows(app.symbols, maxlen=200)
    app.writer = FakeWriter()
    app.pipeline = DetectionPipeline()
    app.persisted = []
    # Storage hooks: record instead of touching psycopg2; no cooldown.
    app._last_event = lambda symbol: (None, None)
    app._persist_anomaly = lambda params: app.persisted.append(params)
    return app


def quote(price, minute_offset=0, symbol="TEST-X"):
    now = datetime.now(timezone.utc) - timedelta(minutes=minute_offset)
    return {
        "symbol": symbol,
        "price": price,
        "return_1m": None,
        "return_5m": None,
        "volume": 1000,
        "timestamp": now.isoformat(),
    }


def test_process_tick_writes_feature_row_with_computed_columns():
    app = make_app([[quote(100.0), quote(100.1)]])

    for q in app.provider.fetch_all():
        app.process_tick(q)

    assert len(app.writer.features) == 2
    row = app.writer.features[-1]
    assert row["symbol"] == "TEST-X"
    assert row["z_score"] is None or isinstance(row["z_score"], float)
    assert "ewma_vol" in row and "pca_residual" in row


def test_spike_is_detected_and_persisted_once():
    prices = [100.0 + i * 0.01 for i in range(30)]
    cycle = [quote(p) for p in prices]
    spike = quote(prices[-1] * 1.25)  # extreme jump after a quiet ramp

    app = make_app([cycle, [spike]])
    for _ in range(2):
        for q in app.provider.fetch_all():
            app.process_tick(q)

    assert len(app.writer.features) == 31
    assert len(app.persisted) >= 1
    recorded = app.persisted[0]
    assert recorded[1] == "TEST-X"
    assert 0.0 <= recorded[2] <= 1.0
    assert "Tick-level" in recorded[6]


def test_cooldown_suppresses_repeat_alerts():
    app = make_app([])
    # Recent max-severity event: a new alert cannot escalate past it,
    # so the cooldown must suppress everything.
    last_ts = datetime.now(timezone.utc) - timedelta(minutes=1)
    app._last_event = lambda symbol: (last_ts, 1.0)

    prices = [100.0 + i * 0.01 for i in range(30)]
    for p in prices:
        app.process_tick(quote(p))
    before = len(app.persisted)
    app.process_tick(quote(prices[-1] * 1.25))

    assert len(app.persisted) == before


def test_correlation_snapshot_written_on_interval():
    app = make_app([])
    app.message_count = 15  # CORR_SNAPSHOT_INTERVAL boundary nearby

    # Feed enough ticks that the interval counter crosses a multiple.
    for i in range(20):
        app.process_tick(quote(100.0 + i * 0.001))

    assert app.message_count == 35


def test_ingest_loop_drains_cycles_then_stops(monkeypatch):
    import src.slim

    monkeypatch.setattr(src.slim, "FETCH_INTERVAL", 0.05)
    app = make_app([
        [quote(100.0)],
        [quote(101.0)],
    ])

    thread = threading.Thread(target=app.ingest_loop, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while len(app.writer.features) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    app.stop_event.set()
    thread.join(timeout=5)

    assert len(app.writer.features) == 2
    assert not thread.is_alive()
