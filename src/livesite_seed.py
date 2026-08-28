"""
One-time historical backfill for the live-site's first runs.

Two strategies, used in order until at least one returns data:

1. Coinbase daily candles for crypto symbols (keyless, works from any
   cloud network, no anti-bot walls). Coinbase serves roughly 300 days
   of history at a granularity we won't pretend is more granular than
   daily.
2. yfinance hourly bars for every tracked symbol (the backtest path).
   Works from user devices; on cloud runners it may return nothing if
   the IP is throttled — that's fine, Coinbase already covered crypto.

Returns a flat list of feature-shape dicts (oldest-first per symbol).
On total failure, returns [] so the live run can still proceed.
"""

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def _coinbase_daily(symbol: str, granularity: str = "ONE_DAY",
                   days: int = 180) -> list[dict[str, Any]]:
    """One symbol's daily candles from Coinbase public market-data API."""
    import requests
    from datetime import datetime, timedelta, timezone

    product = f"{symbol.split('-')[0]}-USD" if "-" in symbol else symbol
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    url = f"https://api.exchange.coinbase.com/products/{product}/candles"
    rows: list[dict[str, Any]] = []
    for window_start in pd.date_range(start, end, freq="6d"):
        window_end = min(window_start + pd.Timedelta(days=6), end)
        try:
            resp = requests.get(
                url,
                params={
                    "start": window_start.isoformat(),
                    "end": window_end.isoformat(),
                    "granularity": granularity,
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning("[SEED] coinbase %s: %s", symbol, e)
            continue
        if resp.status_code != 200 or not resp.text.strip():
            continue
        try:
            for candle in resp.json():
                _, low, high, open_, close, _volume, ts = candle[:7]
                rows.append({
                    "timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                    "symbol": symbol,
                    "price": round(float(close), 4),
                    "return_1m": None,
                    "return_5m": None,
                    "volume": None,
                })
        except (ValueError, KeyError) as e:
            logger.warning("[SEED] coinbase %s parse: %s", symbol, e)
    return rows


def seed_history(symbols, periods: int = 180, interval: str = "1h") -> list[dict]:
    """
    Fetch historical closes and return them as feature-shape rows.

    Best-effort: any per-source failure is logged and skipped. The
    function returns whatever any source yielded; the caller treats
    empty as a non-fatal cold start.
    """
    rows: list[dict] = []

    # Coinbase daily for crypto — works from any network.
    crypto = [s for s in symbols if s.endswith("-USD")]
    for sym in crypto:
        coinbase_rows = _coinbase_daily(sym, days=periods)
        if coinbase_rows:
            rows.extend(coinbase_rows)
            logger.info(
                "[SEED] coinbase %s: %d daily candles",
                sym, len(coinbase_rows),
            )

    # yfinance hourly for everything (the backtest path). Fills in
    # anything Coinbase didn't cover, and is the primary source on
    # user devices where Yahoo isn't throttled.
    try:
        from src.backtest.data import align_panel, fetch_symbol_history
    except ImportError as e:
        logger.error("[SEED] backtest module unavailable: %s", e)
    else:
        period = f"{periods}d"
        series: dict[str, Any] = {}
        for sym in symbols:
            try:
                series[sym] = fetch_symbol_history(
                    sym, period=period, interval=interval
                )
            except Exception as e:
                logger.warning("[SEED] yfinance %s: %s", sym, e)
        if series:
            try:
                panel = align_panel(
                    series, freq="h" if interval.endswith("h") else "D"
                )
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
            except Exception as e:
                logger.error("[SEED] yfinance align: %s", e)

    rows.sort(key=lambda r: (r["symbol"], r["timestamp"]))
    if rows:
        per_sym = {s: sum(1 for r in rows if r["symbol"] == s) for s in symbols}
        logger.info(
            "[SEED] total: %d rows; per-symbol %s",
            len(rows), per_sym,
        )
    else:
        logger.warning(
            "[SEED] no rows fetched — every source was blocked or empty"
        )
    return rows
