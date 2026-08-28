"""
One-time historical backfill for the live-site's first run.

Uses the same yfinance-based loader proven by the backtest harness
(~30s, ~150KB, 738 rows × 7 symbols in our earlier run). Only invoked
on the first run when no carried-over state exists, so this cost is
paid exactly once per branch lifecycle.

Returns a flat list of feature-shape dicts ready to be loaded into the
FileStore, oldest-first per symbol.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def seed_history(symbols, periods: int = 180, interval: str = "1h") -> list[dict]:
    """
    Fetch historical closes and return them as feature-shape rows.

    Best-effort: any per-symbol failure is logged and skipped. On
    total failure (no data at all), returns [] so the live run can
    continue with an empty warm-start.
    """
    try:
        from src.backtest.data import align_panel, fetch_symbol_history
    except ImportError as e:
        logger.error("[SEED] backtest module unavailable: %s", e)
        return []

    period = f"{periods}d"
    series: dict[str, Any] = {}
    for sym in symbols:
        try:
            series[sym] = fetch_symbol_history(sym, period=period, interval=interval)
        except Exception as e:
            logger.warning("[SEED] fetch failed for %s: %s", sym, e)

    if not series:
        return []

    try:
        panel = align_panel(series, freq="h" if interval.endswith("h") else "D")
    except Exception as e:
        logger.error("[SEED] alignment failed: %s", e)
        return []

    rows: list[dict] = []
    for sym in panel.columns:
        for ts, price in panel[sym].items():
            if price is None or (price != price):
                continue
            rows.append({
                "timestamp": ts.isoformat(),
                "symbol": sym,
                "price": round(float(price), 4),
                "return_1m": None,
                "return_5m": None,
                "volume": None,
            })
    rows.sort(key=lambda r: (r["symbol"], r["timestamp"]))
    logger.info(
        "[SEED] %d symbols x %d bars (%.0f KB on disk)",
        panel.shape[1], panel.shape[0], len(rows) * 0.05,
    )
    return rows
