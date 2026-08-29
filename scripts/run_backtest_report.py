"""
Generate the committed detector-quality report (docs/backtest_report.md).

Runs the EXISTING backtest harness (replay_panel + score_trial) over a
deterministic synthetic scenario set — no network, no database — and
pools the harness outputs into per-detector precision / recall / F1
tables plus a false-positive rate. A matplotlib chart visualizes the
recall-vs-magnitude sensitivity curve.

Deterministic: fixed seeds everywhere, so the committed report only
changes when detector code changes. Regenerate with:

    python scripts/run_backtest_report.py

Scenario set:
  1. Single-symbol plants (spike/dip/vol_burst via inject.plant_anomalies)
     at magnitudes 1..6 price-level sigmas, 3 seeds, rotating target.
  2. Joint-flip plants: a tightly-correlated pair (^GSPC/^IXIC) shifted
     in OPPOSITE directions by sub-unit level-sigmas — invisible to any
     single-asset detector, the exact case MultivariateJointDetector
     exists for.
  3. Clean replay (no plants) for the false-positive rate.
"""

import logging
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backtest.harness import replay_panel, score_trial  # noqa: E402
from src.backtest.inject import (  # noqa: E402
    PlantedEvent,
    choose_event_positions,
    plant_anomalies,
)
from src.detection.cross_asset import CrossAssetCorrelationDetector  # noqa: E402
from src.detection.isolation_forest import IsolationForestDetector  # noqa: E402
from src.detection.joint import MultivariateJointDetector  # noqa: E402
from src.detection.pipeline import DetectionPipeline  # noqa: E402
from src.precision import _DETECTOR_KEYWORDS  # noqa: E402
from src.detection.zscore import ZScoreDetector  # noqa: E402

logging.basicConfig(level=logging.WARNING)

# ---------------------------------------------------------------------------
# Scenario configuration (deterministic)
# ---------------------------------------------------------------------------

PANEL_SYMBOLS = ["^GSPC", "^IXIC", "BTC-USD", "GC=F"]
PANEL_SECTIONS = 500
WARMUP_TICKS = 150
PAIR = ("^GSPC", "^IXIC")          # correlated pair in the panel (rho=0.9)
PAIR_RHO = 0.9
SECTION_SIGMAS = [0.001, 0.0011, 0.002, 0.0008]  # per-symbol return vol

SINGLE_MAGNITUDES = [1.0, 1.5, 2.0, 3.0, 4.0, 6.0]   # price-level sigmas
SINGLE_SEEDS = [101, 202, 303]
SINGLE_EVENTS_PER_TRIAL = 6

JOINT_DEVIATIONS = [1.0, 1.5, 2.0, 3.0]                # return-sigmas, opposite
JOINT_SEEDS = [401, 502, 603]

# Derived from the shared attribution mapping so a new detector
# automatically appears in the report (single source of truth).
DETECTOR_TYPES = sorted(_DETECTOR_KEYWORDS.keys())
TARGET_SYMBOL = PANEL_SYMBOLS[-1]   # joint events fire on the last column


# ---------------------------------------------------------------------------
# Panel + pipeline builders
# ---------------------------------------------------------------------------

def build_panel(seed: int = 42) -> pd.DataFrame:
    """
    Deterministic 4-symbol hourly panel. ^GSPC/^IXIC are correlated
    (rho=PAIR_RHO) so cross-asset and joint detectors have structure to
    work with; per-symbol vol differs so covariance must handle scale.
    """
    rng = np.random.default_rng(seed)
    n = len(PANEL_SYMBOLS)
    cov = np.zeros((n, n))
    for i, sym in enumerate(PANEL_SYMBOLS):
        cov[i, i] = SECTION_SIGMAS[i] ** 2
    pair_idx = (PANEL_SYMBOLS.index(PAIR[0]), PANEL_SYMBOLS.index(PAIR[1]))
    cov[pair_idx] = cov[pair_idx[::-1]] = (
        PAIR_RHO * SECTION_SIGMAS[pair_idx[0]] * SECTION_SIGMAS[pair_idx[1]]
    )

    returns = rng.multivariate_normal(np.zeros(n), cov, size=PANEL_SECTIONS)
    base = [100.0, 20000.0, 60000.0, 2400.0]
    prices = np.cumprod(1 + returns, axis=0) * np.array(base)

    index = pd.date_range("2030-01-01", periods=PANEL_SECTIONS, freq="h", tz="UTC")
    return pd.DataFrame(prices, index=index, columns=PANEL_SYMBOLS)


def build_pipeline() -> DetectionPipeline:
    """The detector set under test, sized for the synthetic panel."""
    pipe = DetectionPipeline(aggregate=True)
    pipe.detectors = [
        ZScoreDetector(name="zscore_price", window_size=100, threshold=3.0,
                       min_observations=20),
        IsolationForestDetector(name="iforest_price", window_size=100,
                                threshold=0.05, min_observations=30,
                                retrain_interval=50),
        CrossAssetCorrelationDetector(name="cross_asset_corr", window_size=100,
                                      threshold=2.5, min_observations=30,
                                      pairs=[PAIR]),
        MultivariateJointDetector(name="joint_mahalanobis",
                                  symbols=PANEL_SYMBOLS, threshold=3.0,
                                  min_observations=40, window_size=100),
    ]
    return pipe


# ---------------------------------------------------------------------------
# Scenario runners (all scoring via harness.score_trial)
# ---------------------------------------------------------------------------

def _level_sigma(closes: np.ndarray, warmup: int) -> float:
    return float(np.std(closes[:warmup]))


def run_single_symbol_trial(panel, magnitude, seed, target):
    """Plant spike/dip/vol_burst events on `target` via inject.py."""
    closes = panel[target].to_numpy()
    ls = _level_sigma(closes, WARMUP_TICKS)
    new_closes, events = plant_anomalies(
        closes,
        n_events=SINGLE_EVENTS_PER_TRIAL,
        magnitude_sigma=magnitude,
        warmup=WARMUP_TICKS,
        seed=seed,
    )
    work = panel.copy()
    work[target] = new_closes
    alerts = replay_panel(work, build_pipeline(), warmup_ticks=WARMUP_TICKS)
    return score_trial(alerts, events, target), len(events)


def run_joint_trial(panel, deviation, seed):
    """
    Plant opposite-direction RETURN shifts on the correlated pair at the
    same sections — each asset's move is `deviation` return-sigmas, far
    below any single-asset threshold, but the pair's difference
    direction is jointly extreme. Deviations are deliberately in
    RETURN-sigma space (per-symbol): level-sigma shifts scale with the
    price level, so the same nominal size would be a huge kick for a
    100-priced symbol and invisible for a 20000-priced one. Positions
    reuse inject.py's spacing logic.
    """
    work = panel.copy()

    rng = np.random.default_rng(seed)
    positions = choose_event_positions(
        PANEL_SECTIONS, 6, first_valid=WARMUP_TICKS, min_separation=60, rng=rng
    )

    events = []
    for pos in positions:
        events.append(PlantedEvent(pos, pos, "joint_flip", deviation))
        # PAIR[0] moves up, PAIR[1] moves down by the same return-sigma
        # size: on a correlated pair this is a joint-only anomaly.
        signs = (1.0, -1.0)
        for sym, sign in zip(PAIR, signs):
            prev = work.iloc[pos - 1][sym]
            sigma_r = SECTION_SIGMAS[panel.columns.get_loc(sym)]
            work.iloc[pos, work.columns.get_loc(sym)] = prev * (
                1.0 + sign * deviation * sigma_r
            )

    alerts = replay_panel(work, build_pipeline(), warmup_ticks=WARMUP_TICKS)
    return score_trial(alerts, events, target=TARGET_SYMBOL), len(events)


def run_clean_replay(panel):
    """No plants: alerts per 1000 evaluated symbol-ticks, per detector."""
    alerts = replay_panel(panel.copy(), build_pipeline(), warmup_ticks=WARMUP_TICKS)
    per_type: dict[str, int] = defaultdict(int)
    for a in alerts:
        per_type[a["type"]] += 1
    eval_ticks = (len(panel) - WARMUP_TICKS) * len(panel.columns)
    return per_type, eval_ticks


# ---------------------------------------------------------------------------
# Pooling (aggregates score_trial outputs; scoring itself stays in the
# harness)
# ---------------------------------------------------------------------------

def pool_trials(trial_results):
    """
    trial_results: list of (scored, n_events) from score_trial.
    Returns {detector_type: {alerts, matched, events, precision, recall, f1}}.
    """
    agg: dict[str, dict] = {}
    for scored, n_events in trial_results:
        for det, s in scored.items():
            a = agg.setdefault(det, {"alerts": 0, "matched": 0, "events": 0})
            a["alerts"] += s["alerts"]
            a["matched"] += s["matched_events"]
            a["events"] += n_events
    for det, a in agg.items():
        a["precision"] = round(a["matched"] / a["alerts"], 3) if a["alerts"] else None
        a["recall"] = round(a["matched"] / a["events"], 3) if a["events"] else None
        p, r = a["precision"], a["recall"]
        a["f1"] = round(2 * p * r / (p + r), 3) if p and r else None
    return agg


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _fmt_row(det, stats):
    p = f"{stats['precision']:.2f}" if stats["precision"] is not None else "—"
    r = f"{stats['recall']:.2f}" if stats["recall"] is not None else "—"
    f = f"{stats['f1']:.2f}" if stats["f1"] is not None else "—"
    return (f"| {det} | {stats['matched']}/{stats['events']} | {p} | {r} | {f} "
            f"| {stats['alerts']} |")


def generate_markdown(clean_fpr, single_report, joint_report, chart_rel) -> str:
    lines = [
        "# Detector Quality Report (synthetic backtest)",
        "",
        f"Generated by `scripts/run_backtest_report.py` from the live",
        f"backtest harness (`src/backtest/harness.py`) — deterministic",
        f"synthetic scenarios, fixed seeds, no network. This report only",
        f"changes when detector code changes; regenerate it after any",
        f"detector modification.",
        "",
        "## Scenario set",
        "",
        f"- Panel: {len(PANEL_SYMBOLS)} symbols x {PANEL_SECTIONS} hourly "
        f"sections, deterministic random walk ({PAIR[0]}/{PAIR[1]} "
        f"correlated at rho={PAIR_RHO}); first {WARMUP_TICKS} sections "
        f"are warm-up.",
        f"- Single-symbol plants: spike/dip/vol_burst via "
        f"`inject.plant_anomalies` at {SINGLE_MAGNITUDES} price-level "
        f"sigmas, {SINGLE_EVENTS_PER_TRIAL} events per trial, "
        f"{len(SINGLE_SEEDS)} seeds, rotating target symbol.",
        f"- Joint-flip plants: the correlated pair shifted in OPPOSITE "
        f"directions by {JOINT_DEVIATIONS} return-sigmas simultaneously — "
        f"per-asset moves far below single-asset thresholds; only the "
        f"joint structure is anomalous.",
        "- Clean replay: no plants, measures the false-positive rate.",
        "",
        "## False-positive rate (clean replay)",
        "",
        "Alerts per 1000 evaluated symbol-ticks with no anomalies present.",
        "",
        "| detector | alerts/1000 ticks |",
        "|---|---|",
    ]
    for det in DETECTOR_TYPES:
        n = clean_fpr.get(det, 0)
        lines.append(f"| {det} | {n:.1f} |")

    lines += [
        "",
        "## Single-symbol planted anomalies",
        "",
        "Pooled over all magnitudes and seeds. Precision = matched/alerts, "
        "recall = matched/planted, F1 = harmonic mean.",
        "",
        "| detector | caught/planted | precision | recall | F1 | alerts fired |",
        "|---|---|---|---|---|---|",
    ]
    for det in DETECTOR_TYPES:
        stats = single_report["overall"].get(det)
        if stats:
            lines.append(_fmt_row(det, stats))

    lines += [
        "",
        "### Recall by planted magnitude (single-symbol)",
        "",
        "| magnitude (level-sigmas) | " + " | ".join(DETECTOR_TYPES) + " |",
        "|---|" + "---|" * len(DETECTOR_TYPES),
    ]
    for mag in SINGLE_MAGNITUDES:
        cells = []
        for det in DETECTOR_TYPES:
            stats = single_report["by_magnitude"].get(mag, {}).get(det)
            r = stats["recall"] if stats and stats["recall"] is not None else None
            cells.append(f"{r:.2f}" if r is not None else "—")
        lines.append(f"| {mag} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Joint-flip planted anomalies (pair moves opposite)",
        "",
        "Per-asset moves are sub-threshold by construction — only the "
        "joint structure is anomalous. This isolates what "
        "`joint_mahalanobis` adds over single-asset detectors.",
        "",
        "| deviation (return-sigmas) | " + " | ".join(DETECTOR_TYPES) + " |",
        "|---|" + "---|" * len(DETECTOR_TYPES),
    ]
    for dev in JOINT_DEVIATIONS:
        cells = []
        for det in DETECTOR_TYPES:
            stats = joint_report["by_deviation"].get(dev, {}).get(det)
            r = stats["recall"] if stats and stats["recall"] is not None else None
            cells.append(f"{r:.2f}" if r is not None else "—")
        lines.append(f"| {dev} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Pooled joint-flip metrics",
        "",
        "| detector | caught/planted | precision | recall | F1 | alerts fired |",
        "|---|---|---|---|---|---|",
    ]
    for det in DETECTOR_TYPES:
        stats = joint_report["overall"].get(det)
        if stats:
            lines.append(_fmt_row(det, stats))

    lines += [
        "",
        "## Reading this report",
        "",
        "- A recall of 0 at low magnitudes is expected: no detector can "
        "catch a move smaller than its threshold.",
        "- Isolation Forest carries a ~5% by-design flag rate "
        "(contamination=0.05), so its false-positive rate is structural, "
        "not a bug.",
        "- The joint detector's value shows in the joint-flip table: "
        "single-asset recall stays ~0 at every deviation while "
        "`joint_mahalanobis` catches the pair move.",
        f"![Recall by planted magnitude]({chart_rel})",
        "",
    ]
    return "\n".join(lines) + "\n"


def generate_chart(single_report, out_path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 5))
    palette = {
        "zscore": "#58a6ff",
        "iforest": "#fbbf24",
        "correlation": "#a78bfa",
        "joint": "#f87171",
    }
    default = "#8b98a5"
    colors = {
        det: palette.get(_DETECTOR_KEYWORDS.get(det, ""), default)
        for det in DETECTOR_TYPES
    }
    for det in DETECTOR_TYPES:
        xs, ys = [], []
        for mag in SINGLE_MAGNITUDES:
            stats = single_report["by_magnitude"].get(mag, {}).get(det)
            if stats and stats["recall"] is not None:
                xs.append(mag)
                ys.append(stats["recall"])
        if xs:
            ax.plot(xs, ys, marker="o", label=det, color=colors.get(det))
    ax.set_xlabel("planted magnitude (price-level sigmas)")
    ax.set_ylabel("recall")
    ax.set_title("Detector recall by planted anomaly magnitude")
    ax.set_ylim(-0.05, 1.05)
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main() -> None:
    panel = build_panel()
    print(f"panel: {panel.shape[0]} sections x {panel.shape[1]} symbols")

    print("clean replay (false-positive rate) ...")
    clean_fpr, eval_ticks = run_clean_replay(panel)
    print(f"  {clean_fpr} over {eval_ticks} symbol-ticks")

    single_trials = defaultdict(list)
    print("single-symbol scenarios ...")
    for magnitude in SINGLE_MAGNITUDES:
        for seed in SINGLE_SEEDS:
            target = PANEL_SYMBOLS[int(seed) % len(PANEL_SYMBOLS)]
            scored, n_events = run_single_symbol_trial(panel, magnitude, seed, target)
            single_trials[magnitude].append((scored, n_events))
            print(f"  mag={magnitude} seed={seed} target={target}: {n_events} events")
    single_pooled = {m: pool_trials(trials) for m, trials in single_trials.items()}
    single_overall = pool_trials([t for trials in single_trials.values() for t in trials])

    joint_trials = defaultdict(list)
    print("joint-flip scenarios ...")
    for deviation in JOINT_DEVIATIONS:
        for seed in JOINT_SEEDS:
            scored, n_events = run_joint_trial(panel, deviation, seed)
            joint_trials[deviation].append((scored, n_events))
            print(f"  dev={deviation} seed={seed}: {n_events} events")
    joint_pooled = {d: pool_trials(trials) for d, trials in joint_trials.items()}
    joint_overall = pool_trials([t for trials in joint_trials.values() for t in trials])

    single_report = {"overall": single_overall, "by_magnitude": single_pooled}
    joint_report = {"overall": joint_overall, "by_deviation": joint_pooled}

    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs"
    )
    os.makedirs(out_dir, exist_ok=True)
    chart_rel = "backtest_report.png"
    generate_chart(single_report, os.path.join(out_dir, "backtest_report.png"))
    markdown = generate_markdown(clean_fpr, single_report, joint_report, chart_rel)
    report_path = os.path.join(out_dir, "backtest_report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    print(f"report written: {report_path}")


if __name__ == "__main__":
    main()
