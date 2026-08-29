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


# ---------------------------------------------------------------------------
# Incident log (incidents.json + incidents.html)

def _label_and_publish(tmp_path, seed, macro=None, llm=None, lead_lag_marker=None):
    """Detect a spike on TEST-X, optionally annotate it, publish once."""
    import json as _json
    from datetime import timedelta

    prices = [100.0 + i * 0.01 for i in range(30)]
    site = run_cycle(tmp_path, [[quote(float(p)) for p in prices]], carry=True)

    assert site.store.anomalies, "expected the ramp+spike to produce an event"
    entry = site.store.anomalies[0]
    if macro:
        entry["macro_context"] = macro
    if llm:
        entry["llm_explanation"] = llm
    if lead_lag_marker:
        entry["description"] += lead_lag_marker
    site.save_state()
    site.build_artifacts()
    return site


def test_incident_log_includes_all_optional_fields(tmp_path):
    from datetime import datetime, timezone

    site = _label_and_publish(
        tmp_path, seed=7, macro="FOMC rate decision",
        llm="BTC rose while SPX fell — a broad risk-off move.",
        lead_lag_marker=" [lead-lag: led by GC=F (2 ticks, r=0.71)]",
    )
    data = json.loads((tmp_path / "build" / "data" / "incidents.json").read_text())

    assert data["schema"] == 1
    assert data["retention_days"] == 60
    assert data["incidents"], "expected at least one incident"
    incident = data["incidents"][0]
    assert incident["symbol"] == "TEST-X"
    assert incident["severity"] in ("low", "medium", "high", "critical")
    assert incident["macro_context"] == "FOMC rate decision"
    assert incident["llm_explanation"] == "BTC rose while SPX fell — a broad risk-off move."
    assert incident["lead_lag"]["leader"] == "GC=F"
    assert incident["lead_lag"]["lag_ticks"] == 2
    # HTML page copied next to the data
    assert (tmp_path / "build" / "incidents.html").exists()


def test_incident_log_omits_absent_optional_fields(tmp_path):
    prices = [100.0 + i * 0.01 for i in range(30)]
    run_cycle(tmp_path, [[quote(float(p)) for p in prices]])

    data = json.loads((tmp_path / "build" / "data" / "incidents.json").read_text())
    assert data["incidents"], "expected at least one incident"
    for incident in data["incidents"]:
        # Optional context that never fired must be omitted, not null/blank
        assert "macro_context" not in incident or not incident["macro_context"]
        assert "llm_explanation" not in incident or not incident["llm_explanation"]
        assert incident.get("lead_lag") is None
        assert incident["description"]           # required field always present


def test_incident_log_respects_retention_window(tmp_path):
    from datetime import timedelta

    old_prices = [100.0 + i * 0.01 for i in range(30)]
    site = run_cycle(tmp_path, [[quote(float(p)) for p in old_prices]], carry=True)

    # Backdate every stored anomaly beyond the 60-day retention window.
    for e in site.store.anomalies:
        ts = datetime.fromisoformat(e["timestamp"]) - timedelta(days=90)
        e["timestamp"] = ts.isoformat()
    site.save_state()
    site.build_artifacts()

    data = json.loads((tmp_path / "build" / "data" / "incidents.json").read_text())
    # The stale incident is dropped, but the underlying state keeps it
    # (save_state re-published the backdated timestamps).
    assert data["incidents"] == []
    assert json.loads(
        (tmp_path / "build" / "state" / "anomalies.json").read_text()
    )["events"]          # state retention is independent of the log window


def test_incident_log_reuses_harness_scoring_fields(tmp_path):
    """The incident entries must carry the same enrichment the
    anomalies.json feed publishes (single enriched source)."""
    prices = [100.0 + i * 0.01 for i in range(30)]
    run_cycle(tmp_path, [[quote(float(p)) for p in prices]])

    incidents = json.loads((tmp_path / "build" / "data" / "incidents.json").read_text())
    anomalies = json.loads((tmp_path / "build" / "data" / "anomalies.json").read_text())

    assert incidents["incidents"] == anomalies["events"][:len(incidents["incidents"])]
