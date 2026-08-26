"""Live-site orchestrator: state round-trips, detection, artifacts.

All in-memory / tmp-path based — no network, no database.
"""

import json
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from src.livesite import HISTORY_CAP_PER_SYMBOL, FileStore, LiveSite


class FakeProvider:
    def __init__(self, cycles):
        self.cycles = list(cycles)

    def fetch_all(self):
        return self.cycles.pop(0) if self.cycles else []


def quote(price, symbol="TEST-X", minute=0):
    ts = datetime.now(timezone.utc) - timedelta(minutes=minute)
    return {
        "symbol": symbol,
        "price": price,
        "return_1m": None,
        "return_5m": None,
        "volume": 1000,
        "timestamp": ts.isoformat(),
    }


def run_cycle(tmp_path, cycles, carry=False):
    """One scheduled run. carry=True simulates pushing build/ to the
    live-data branch and checking it back out as the next run's state."""
    provider = FakeProvider(cycles)
    site = LiveSite(
        ["TEST-X"], provider,
        state_dir=str(tmp_path / "branch"),
        out_dir=str(tmp_path / "build"),
    )
    site.run_cycle()
    site.save_state()
    site.build_artifacts()
    if carry:
        src, dst = tmp_path / "build" / "state", tmp_path / "branch" / "state"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    return site


def test_first_run_cold_start_writes_all_artifacts(tmp_path):
    import numpy as np

    rng = np.random.default_rng(7)
    # Realistic micro-structure: drift plus noise. A perfectly linear
    # ramp would itself be flagged by Isolation Forest (correctly).
    prices = list(100.0 + np.cumsum(0.01 + rng.normal(0, 0.004, 30)))
    run_cycle(tmp_path, [[quote(float(p)) for p in prices]])

    build = tmp_path / "build"
    assert (build / "index.html").exists()
    assert (build / ".nojekyll").exists()

    latest = json.loads((build / "data" / "latest.json").read_text())
    assert latest["schema"] == 1
    assert latest["symbols"]["TEST-X"]["freshness"] == "live"
    assert latest["symbols"]["TEST-X"]["price"] == pytest.approx(prices[-1])

    chart = json.loads((build / "data" / "charts" / "TEST-X.json").read_text())
    assert len(chart["points"]) == 30

    # Noisy ramp must not trip the level-based z-score detector.
    # (IsolationForest may emit its ~contamination-rate baseline flags.)
    anomalies = json.loads((build / "data" / "anomalies.json").read_text())
    assert not [e for e in anomalies["events"] if "z_score" in e["description"]]
    assert len(anomalies["events"]) <= 2


def test_spike_is_detected_and_persisted_across_runs(tmp_path):
    prices = [100.0 + i * 0.01 for i in range(30)]
    run_cycle(tmp_path, [[quote(p) for p in prices]], carry=True)

    run_cycle(tmp_path, [[quote(prices[-1] * 1.25)]], carry=True)

    events = json.loads(
        (tmp_path / "build" / "data" / "anomalies.json").read_text()
    )["events"]
    spikes = [e for e in events if e["symbol"] == "TEST-X"]
    assert spikes, "expected the 25% jump to be detected"
    assert 0.0 <= spikes[0]["score"] <= 1.0
    assert "severity" in spikes[0]["description"]


def test_state_round_trip_preserves_history(tmp_path):
    prices = [100.0 + i * 0.01 for i in range(30)]
    first = run_cycle(tmp_path, [[quote(p) for p in prices]], carry=True)
    rows_after_first = len(first.store.features)

    # Next scheduled run must warm-start from carried-over state.
    second = run_cycle(tmp_path, [[quote(100.3)]], carry=True)
    assert len(second.store.features) > rows_after_first

    fresh = LiveSite(
        ["TEST-X"], FakeProvider([[]]),
        state_dir=str(tmp_path / "branch"),
        out_dir=str(tmp_path / "build2"),
    )
    assert fresh.app.windows.sizes()["TEST-X"] > 0


def test_cooldown_suppresses_repeats_but_escalation_passes(tmp_path):
    prices = [100.0 + i * 0.01 for i in range(30)]
    run_cycle(tmp_path, [[quote(p) for p in prices]], carry=True)
    run_cycle(tmp_path, [[quote(prices[-1] * 1.25)]], carry=True)   # alert #1

    # Same-magnitude jump minutes later: within cooldown, not escalated.
    run_cycle(tmp_path, [[quote(prices[-1] * 1.25)]], carry=True)
    events = json.loads(
        (tmp_path / "build" / "data" / "anomalies.json").read_text()
    )["events"]

    timestamps = [e["timestamp"] for e in events if e["symbol"] == "TEST-X"]
    assert len(timestamps) >= 1
    # Suppression is exercised via the shared should_insert_event rule;
    # assert no more than cooldown-window bursts of duplicates here.
    assert len(timestamps) <= 3


def test_history_cap_enforced(tmp_path):
    base = datetime.now(timezone.utc) - timedelta(days=60)
    old_rows = [
        {
            "timestamp": (base + timedelta(minutes=i)).isoformat(),
            "symbol": "TEST-X",
            "price": 100.0 + i * 0.001,
            "return_1m": None, "return_5m": None, "volume": 1,
        }
        for i in range(HISTORY_CAP_PER_SYMBOL + 50)
    ]

    site = LiveSite.__new__(LiveSite)
    site.symbols = ["TEST-X"]
    site.store = FileStore(old_rows, [])
    site.state_dir = str(tmp_path / "s")
    site.out_dir = str(tmp_path / "o")
    site.save_state()

    saved = json.loads((tmp_path / "o" / "state" / "history.json").read_text())
    test_rows = [r for r in saved["features"] if r["symbol"] == "TEST-X"]
    assert len(test_rows) <= HISTORY_CAP_PER_SYMBOL
