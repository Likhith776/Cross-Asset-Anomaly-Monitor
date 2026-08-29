"""
Backtest CLI: run detectors over real market history.

Two experiments:
  1. Clean replay — real history through an untouched pipeline measures
     each detector's false-alarm rate (alerts/day), plus a z-score
     threshold sweep showing how the alarm budget trades against
     sensitivity.
  2. Planted anomalies — labeled spike/dip/vol-burst events at known
     positions measure recall, precision, and detection lag per detector.

Usage: python -m src.backtest [--period 180d] [--interval 1h]
                              [--trials 5] [--events-per-trial 6]
                              [--magnitudes 4 8] [--json PATH]

Requires network for the first run (yfinance); results are cached in
.backtest_cache/ afterwards.
"""

import argparse
import json
import logging
import warnings
from collections import defaultdict

import numpy as np

from src.backtest.data import align_panel, fetch_symbol_history
from src.backtest.harness import replay_panel, score_trial
from src.backtest.inject import DEFAULT_WARMUP, plant_anomalies
from src.detection.pipeline import DetectionPipeline
from src.precision import _DETECTOR_KEYWORDS
from src.universe import load_universe

logger = logging.getLogger("backtest")

SYMBOLS = list(load_universe().symbols)
ZSCORE_THRESHOLDS = [2.5, 3.0, 3.5]


def load_panel(args):
    series = {}
    for sym in args.symbols:
        logger.info("loading %s ...", sym)
        series[sym] = fetch_symbol_history(
            sym, period=args.period, interval=args.interval, cache_dir=args.cache_dir
        )
    freq = "h" if args.interval.endswith("h") else "D"
    return align_panel(series, freq=freq)


def aggregate_alerts(alerts) -> dict:
    """alerts per detector type for the clean replay."""
    counts: dict[str, int] = defaultdict(int)
    for a in alerts:
        counts[a["type"]] += 1
    return dict(counts)


def run_clean_replays(panel, warmup_ticks):
    """One pass per z-score threshold; returns {threshold: alert counts}."""
    results = {}
    span_days = (panel.index[-1] - panel.index[0]).total_seconds() / 86400
    for thr in ZSCORE_THRESHOLDS:
        pipe = DetectionPipeline(aggregate=False)
        for det in pipe.detectors:
            if det.name == "zscore_price":
                det.threshold = thr
        alerts = replay_panel(panel, pipe, warmup_ticks=warmup_ticks)
        per_type = aggregate_alerts(alerts)
        results[thr] = {
            "per_day": {t: round(c / span_days, 3) for t, c in per_type.items()},
            "total": sum(per_type.values()),
        }
    return results, span_days


def run_planted_trials(panel, args):
    """Plant labeled events into one symbol per trial and score recall."""
    warmup_ticks = max(DEFAULT_WARMUP, 150)
    eval_panel = panel.iloc[warmup_ticks:]
    rng_master = np.random.default_rng(42)

    # Per-magnitude accumulators: detector type -> running stats
    acc = {
        mag: defaultdict(lambda: {"alerts": 0, "matched": 0, "n_events": 0,
                                  "precisions": [], "lags": []})
        for mag in args.magnitudes
    }

    trials_per_mag = args.trials
    symbols = list(panel.columns)
    for mag in args.magnitudes:
        for trial in range(trials_per_mag):
            target = symbols[trial % len(symbols)]
            seed = int(rng_master.integers(0, 2**31 - 1))

            work = panel.copy()
            closes = work[target].to_numpy()
            planted_closes, events = plant_anomalies(
                closes,
                n_events=args.events_per_trial,
                magnitude_sigma=mag,
                warmup=warmup_ticks,
                seed=seed,
            )
            work[target] = planted_closes

            # aggregate=False: score every detector independently, unlike
            # the live consumer which keeps only the top-scoring alert.
            pipe = DetectionPipeline(aggregate=False)
            alerts = replay_panel(work, pipe, warmup_ticks=warmup_ticks)
            scored = score_trial(alerts, events, target=target)

            stats = acc[mag]
            total_events = len(events)
            seen_types = set(scored.keys())
            for t, s in scored.items():
                st = stats[t]
                st["alerts"] += s["alerts"]
                st["matched"] += s["matched_events"]
                if s["precision"] is not None:
                    st["precisions"].append(s["precision"])
                if s["median_lag"] is not None:
                    st["lags"].append(s["median_lag"])
                st["n_events"] += total_events
            # Detectors silent this trial still accrue event totals:
            for t in sorted(_DETECTOR_KEYWORDS):
                if t not in seen_types:
                    stats[t]["n_events"] += total_events
            logger.info(
                "[trial mag=%.1f #%d target=%s] %d events, alerts by type: %s",
                mag, trial, target, total_events,
                {t: s["alerts"] for t, s in scored.items()},
            )

    report = {}
    for mag, stats in acc.items():
        rows = {}
        for t, st in stats.items():
            recall = round(st["matched"] / st["n_events"], 3) if st["n_events"] else None
            precision = (
                round(float(np.mean(st["precisions"])), 3) if st["precisions"] else None
            )
            lag = int(np.median(st["lags"])) if st["lags"] else None
            rows[t] = {
                "events": st["n_events"],
                "caught": st["matched"],
                "recall": recall,
                "precision": precision,
                "median_lag_ticks": lag,
            }
        report[mag] = rows
    return report


def main() -> None:
    parser = argparse.ArgumentParser(prog="src.backtest")
    parser.add_argument("--period", default="180d")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--symbols", nargs="*", default=SYMBOLS)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--events-per-trial", type=int, default=6)
    parser.add_argument("--magnitudes", type=float, nargs="*", default=[4.0, 8.0])
    parser.add_argument("--cache-dir", default=".backtest_cache")
    parser.add_argument("--warmup-ticks", type=int, default=max(DEFAULT_WARMUP, 150))
    parser.add_argument("--json", default=None, help="also write the report as JSON")
    args = parser.parse_args()

    warnings.filterwarnings("ignore", category=RuntimeWarning)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    panel = load_panel(args)
    span = (panel.index[0], panel.index[-1], len(panel))
    print(f"\nPanel: {len(args.symbols)} symbols x {span[2]} bars "
          f"({span[0]:%Y-%m-%d %H:%M} .. {span[1]:%Y-%m-%d %H:%M} UTC)\n")

    clean, span_days = run_clean_replays(panel, args.warmup_ticks)
    print(f"=== Clean replay of real history ({span_days:.1f} days) ===")
    print(f"{'zscore_price threshold':>22} | " + " | ".join(
        f"{t:>26}" for t in ("zscore_spike", "isolation_forest_outlier",
                             "correlation_break")))
    for thr in ZSCORE_THRESHOLDS:
        r = clean[thr]["per_day"]
        cells = [f"{r.get(t, 0.0):>8}/day ({clean[thr]['total']:>3} tot)"
                 for t in ("zscore_spike", "isolation_forest_outlier",
                           "correlation_break")]
        print(f"{thr:>22} | " + " | ".join(f"{c:>26}" for c in cells))
    print()

    planted = run_planted_trials(panel, args)
    for mag, rows in planted.items():
        print(f"=== Planted anomalies @ {mag} price-sigma "
              f"({args.trials} trials x {args.events_per_trial} events) ===")
        print(f"{'detector':<28} {'caught':>9} {'recall':>8} {'precision':>10} "
              f"{'med_lag':>8}")
        for t, s in rows.items():
            print(f"{t:<28} {s['caught']:>4}/{s['events']:<4} "
                  f"{str(s['recall']):>8} {str(s['precision']):>10} "
                  f"{str(s['median_lag_ticks']):>8}")
        print()

    if args.json:
        payload = {
            "panel": {"symbols": args.symbols, "bars": len(panel),
                      "start": str(panel.index[0]), "end": str(panel.index[-1])},
            "clean_replay_by_threshold": clean,
            "planted": {str(m): r for m, r in planted.items()},
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"JSON report written to {args.json}")


if __name__ == "__main__":
    main()
