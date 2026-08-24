"""Historical data loading for detector backtesting.

Fetches hourly closes via yfinance and caches them as CSV so repeated
runs (and threshold sweeps) don't re-download. The live database only
holds ~a day of ticks, so the backtest needs real exchange history
downloaded on demand instead.
"""

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = ".backtest_cache"


def _cache_path(cache_dir: Path, symbol: str, period: str, interval: str) -> Path:
    safe = symbol.replace("^", "idx_").replace("=", "_")
    return cache_dir / f"{safe}_{period}_{interval}.csv"


def fetch_symbol_history(
    symbol: str,
    period: str = "180d",
    interval: str = "1h",
    cache_dir: str = DEFAULT_CACHE_DIR,
) -> pd.Series:
    """
    Close prices for one symbol (UTC-indexed Series), cached to CSV.

    yfinance is imported lazily so importing this module never requires
    the package or network access.
    """
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache, symbol, period, interval)

    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        series = df.iloc[:, 0]
        series.name = symbol
        return series.astype(float)

    import yfinance as yf

    raw = yf.download(
        symbol,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"yfinance returned no data for {symbol}")

    close = raw["Close"]
    if isinstance(close, pd.DataFrame):  # multi-ticker download shape
        close = close.iloc[:, 0]
    close = close.dropna().astype(float)
    close.index = pd.to_datetime(close.index, utc=True)
    close.name = symbol
    close.to_csv(path)
    logger.info("[BACKTEST-DATA] %s: %d bars cached to %s", symbol, len(close), path)
    return close


def align_panel(series_by_symbol: dict[str, pd.Series], freq: str | None = None) -> pd.DataFrame:
    """
    Inner-join per-symbol close series on timestamps and sort chronologically.

    With `freq` set, timestamps are floored onto that grid first (keeping
    the last bar per slot): yfinance stamps equity hourly bars at :30,
    treasury yields at :20, crypto/futures on the hour — an exact-match
    join across such grids is empty, so normalization is required for
    real data. After flooring, only rows where every symbol has a price
    remain, which is what the cross-asset correlation detector assumes
    when it compares positional windows between pair members.
    """
    prepared = {}
    for sym, s in series_by_symbol.items():
        if freq:
            s = s.copy()
            s.index = s.index.floor(freq)
            s = s[~s.index.duplicated(keep="last")]
        prepared[sym] = s
    panel = pd.concat(prepared, axis=1).dropna(how="any").sort_index()
    return panel
