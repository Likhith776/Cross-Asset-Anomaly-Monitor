"""
Multi-provider market data layer for the producer.

Yahoo Finance is the default source for all symbols: live prices via the
light `fast_info` endpoint (crypto/FX update near real-time; equities,
futures, and indices are delayed ~10-20 min at the source) plus per-ticker
1-minute bars for volume and exact returns. Unchanged symbols are deduped
per cycle. Finnhub is available as an optional upgrade path: set
FINNHUB_API_KEY to route mapped symbols (e.g. crypto) through its
real-time quotes, with automatic yfinance fallback per symbol.

Return semantics: when the published price is a bar close, 1m/5m returns
come exactly from bar history; when it is an intrabar live print, they are
computed timestamp-aware from the provider's price memory so `return_1m`
remains a true ~1-minute return regardless of poll cadence.

Configuration (environment):
    FINNHUB_API_KEY     Finnhub API key (optional — empty disables Finnhub)
    FINNHUB_SYMBOL_MAP  JSON dict overriding the default symbol routing.
"""

import json
import logging
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("data_provider")

# Symbols routed to Finnhub by default. The free tier serves crypto
# exchange symbols (verified: BINANCE:BTCUSDT works); forex (OANDA:*)
# and indices (^GSPC etc.) require paid plans. Symbols NOT in this map
# go straight to yfinance. Extend via FINNHUB_SYMBOL_MAP when your plan
# covers more, e.g. {"EURUSD=X": "OANDA:EUR_USD", "^GSPC": "^GSPC"}.
DEFAULT_FINNHUB_SYMBOL_MAP = {
    "BTC-USD": "BINANCE:BTCUSDT",
}

FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
FINNHUB_TIMEOUT_SECONDS = 10
FINNHUB_MAX_CONSECUTIVE_FAILURES = 3


def safe_pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """
    Compute percentage change, returning None for invalid inputs.

    Handles zero previous price, NaN inputs, and extreme values.
    """
    if (
        current is None
        or previous is None
        or pd.isna(current)
        or pd.isna(previous)
    ):
        return None
    if previous == 0:
        return None
    try:
        change = (current - previous) / abs(previous)
        if not np.isfinite(change):
            return None
        if abs(change) > 1.0:  # >100% move in 1 min is almost certainly bad data
            return None
        return round(float(change), 6)
    except (OverflowError, FloatingPointError):
        return None


class FinnhubProvider:
    """
    Real-time quote provider via Finnhub's REST API.

    Maintains an in-memory price history per symbol (last 6 quotes) to
    derive 1-minute and 5-minute returns from quote polling. Symbols that
    fail repeatedly (unsupported on the current plan, bad mapping) are
    marked dead for the process lifetime so the facade routes them to
    the yfinance fallback instead of wasting API calls.
    """

    def __init__(
        self,
        api_key: str,
        symbol_map: Optional[dict[str, str]] = None,
        timeout: int = FINNHUB_TIMEOUT_SECONDS,
    ):
        self._api_key = api_key
        self._symbol_map = symbol_map or dict(DEFAULT_FINNHUB_SYMBOL_MAP)
        self._timeout = timeout
        self._history: dict[str, deque] = {}
        self._failures: dict[str, int] = {}
        self._dead_symbols: set[str] = set()
        self._session = None

    @property
    def symbols(self) -> list[str]:
        """Symbols this provider currently serves (excluding dead ones)."""
        return [s for s in self._symbol_map if s not in self._dead_symbols]

    def _http_get(self) -> None:
        # Imported lazily so environments without `requests` still work
        # when only the yfinance provider is used.
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers.update({"X-Finnhub-Token": self._api_key})
        return self._session

    def fetch(self) -> dict[str, dict[str, Any]]:
        """
        Fetch one quote per served symbol.

        Returns {symbol: quote} where quote has the standard fields
        (symbol, price, return_1m, return_5m, volume, source).
        Symbols that fail this cycle are simply absent from the result.
        """
        import requests

        results: dict[str, dict[str, Any]] = {}
        session = self._http_get()

        for symbol in self.symbols:
            finnhub_symbol = self._symbol_map[symbol]
            try:
                resp = session.get(
                    FINNHUB_QUOTE_URL,
                    params={"symbol": finnhub_symbol},
                    timeout=self._timeout,
                )
                if resp.status_code == 429:
                    logger.warning(
                        "[FINNHUB] Rate limited (429) — skipping Finnhub for this cycle"
                    )
                    return results
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                self._register_failure(symbol, f"request failed: {e}")
                continue

            price = data.get("c")  # current price
            if price is None or price <= 0 or not np.isfinite(price):
                self._register_failure(symbol, f"invalid quote payload: {data}")
                continue

            self._failures[symbol] = 0

            history = self._history.setdefault(symbol, deque(maxlen=6))
            return_1m = safe_pct_change(float(price), history[-1] if history else None)
            # history[0] is 5 cycles old once 5 previous quotes exist
            return_5m = safe_pct_change(float(price), history[0] if len(history) >= 5 else None)
            history.append(float(price))

            results[symbol] = {
                "symbol": symbol,
                "price": round(float(price), 4),
                "return_1m": return_1m,
                "return_5m": return_5m,
                "volume": None,  # /quote does not include volume
                "source": "finnhub",
            }

        return results

    def _register_failure(self, symbol: str, reason: str) -> None:
        count = self._failures.get(symbol, 0) + 1
        self._failures[symbol] = count
        logger.warning(
            "[FINNHUB] %s for %s (failure %d/%d)",
            reason,
            symbol,
            count,
            FINNHUB_MAX_CONSECUTIVE_FAILURES,
        )
        if count >= FINNHUB_MAX_CONSECUTIVE_FAILURES:
            self._dead_symbols.add(symbol)
            logger.warning(
                "[FINNHUB] Giving up on %s — routing to yfinance for the rest of this run",
                symbol,
            )


class YFinanceProvider:
    """
    Yahoo Finance provider tuned for the freshest data the free feed allows.

    Strategy (per symbol, every poll cycle):

    1. `fast_info` (light endpoint) supplies the live last price. Crypto
       and FX update near real-time here; equities/futures/indices are
       delayed ~10-20 min at the source no matter how fast we poll.
    2. Per-ticker 1-minute bars (`Ticker.history`, not the batched
       `download` endpoint) supply volume, exact bar timestamps, and
       exact 1m/5m returns — and are the fallback when fast_info fails.
    3. When the live price differs from the last bar close (an intrabar
       print), returns are computed timestamp-aware from this provider's
       price memory (price ~60s / ~300s ago), so `return_1m` stays a
       true 1-minute return regardless of poll cadence.
    4. Dedupe: a symbol whose price and last bar timestamp are unchanged
       since its previous publish is skipped, so delayed or closed
       symbols do not spam duplicate rows.
    """

    MEMORY_MAXLEN = 40            # ~20 min of samples at a 30s cadence
    RETURN_TOLERANCE_SECONDS = 15 # how far a memory sample may miss the target age

    def __init__(self):
        self._tickers: dict[str, Any] = {}
        self._memory: dict[str, deque] = {}        # (timestamp, price) samples
        self._last_published: dict[str, Optional[float]] = {}
        self._last_bar_ts: dict[str, Any] = {}

    def _ticker(self, symbol: str) -> Any:
        import yfinance as yf

        if symbol not in self._tickers:
            self._tickers[symbol] = yf.Ticker(symbol)
        return self._tickers[symbol]

    def _live_price(self, symbol: str) -> Optional[float]:
        """Latest price from the light fast_info endpoint, if reachable."""
        try:
            fi = self._ticker(symbol).fast_info
            for key in ("last_price", "lastPrice"):
                try:
                    price = fi[key]
                except (KeyError, IndexError):
                    continue
                if price is not None and float(price) > 0 and np.isfinite(float(price)):
                    return float(price)
        except Exception as e:
            logger.debug("[YFINANCE] fast_info failed for %s: %s", symbol, e)
        return None

    def _bars(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Recent 1-minute bars for a symbol (pre/post market included).

        Uses a 5-day window so weekends and holidays still resolve to the
        last trading session — period='1d' returns nothing for a symbol
        whose market has been closed for a day (e.g. futures on Monday).
        """
        try:
            df = self._ticker(symbol).history(
                period="5d", interval="1m", prepost=True
            )
        except Exception as e:
            logger.debug("[YFINANCE] history failed for %s: %s", symbol, e)
            return None
        if df is None or df.empty or "Close" not in df.columns:
            return None
        return df

    @classmethod
    def _memory_return(
        cls,
        memory: deque,
        price: float,
        now: datetime,
        target_age_seconds: int,
    ) -> Optional[float]:
        """
        Return vs the newest memory sample at least (target - tolerance)
        seconds old — i.e. approximately the price `target_age_seconds` ago.
        """
        cutoff_age = target_age_seconds - cls.RETURN_TOLERANCE_SECONDS
        target = None
        for ts, p in reversed(memory):
            if (now - ts).total_seconds() >= cutoff_age:
                target = p
                break
        return safe_pct_change(price, target)

    def fetch(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """
        Fetch the freshest quote for each symbol.

        Returns {symbol: quote} with the standard fields. Symbols with no
        new data since their previous publish are omitted (dedupe).
        """
        quotes: dict[str, dict[str, Any]] = {}

        for symbol in symbols:
            try:
                bars = self._bars(symbol)
                bar_close = float(bars["Close"].iloc[-1]) if bars is not None else None
                bar_ts = bars.index[-1] if bars is not None else None

                volume = None
                if bars is not None and "Volume" in bars.columns:
                    last_vol = bars["Volume"].iloc[-1]
                    if pd.notna(last_vol):
                        try:
                            volume = int(last_vol)
                        except (ValueError, OverflowError):
                            volume = None

                live = self._live_price(symbol)
                price = live if live is not None else bar_close
                if price is None or price <= 0 or not np.isfinite(price):
                    continue

                # --- Dedupe: nothing new since the previous publish ---
                if (
                    self._last_published.get(symbol) == price
                    and self._last_bar_ts.get(symbol) == bar_ts
                ):
                    continue

                now = datetime.now(timezone.utc)
                memory = self._memory.setdefault(
                    symbol, deque(maxlen=self.MEMORY_MAXLEN)
                )

                if live is not None and bar_close is not None and live != bar_close:
                    # Intrabar live print — timestamp-aware memory returns
                    return_1m = self._memory_return(memory, price, now, 60)
                    return_5m = self._memory_return(memory, price, now, 300)
                else:
                    # Publishing the bar close — exact returns from bars
                    return_1m = None
                    return_5m = None
                    if bars is not None and len(bars) >= 2:
                        return_1m = safe_pct_change(bar_close, float(bars["Close"].iloc[-2]))
                    if bars is not None and len(bars) >= 6:
                        return_5m = safe_pct_change(bar_close, float(bars["Close"].iloc[-6]))

                memory.append((now, price))
                self._last_published[symbol] = price
                self._last_bar_ts[symbol] = bar_ts

                quotes[symbol] = {
                    "symbol": symbol,
                    "price": round(float(price), 4),
                    "return_1m": return_1m,
                    "return_5m": return_5m,
                    "volume": volume,
                    "source": "yfinance",
                }

            except Exception as e:
                logger.error("[YFINANCE] Error building quote for %s: %s", symbol, e)

        return quotes


class MarketDataProvider:
    """
    Facade that assembles quotes for all configured symbols.

    Routing: Finnhub first for its mapped symbols, yfinance for everything
    else — including Finnhub symbols that failed this cycle or were marked
    dead. Falls back to pure yfinance when no API key is configured.
    """

    def __init__(self, symbols: list[str]):
        self._symbols = list(symbols)

        symbol_map = dict(DEFAULT_FINNHUB_SYMBOL_MAP)
        env_map = os.getenv("FINNHUB_SYMBOL_MAP", "").strip()
        if env_map:
            try:
                parsed = json.loads(env_map)
                if isinstance(parsed, dict):
                    symbol_map.update(parsed)
                    logger.info("[PROVIDER] FINNHUB_SYMBOL_MAP override applied")
                else:
                    logger.warning("[PROVIDER] FINNHUB_SYMBOL_MAP is not a dict — ignored")
            except json.JSONDecodeError as e:
                logger.warning("[PROVIDER] FINNHUB_SYMBOL_MAP is not valid JSON — ignored: %s", e)

        # Only route symbols we actually track
        symbol_map = {k: v for k, v in symbol_map.items() if k in self._symbols}

        api_key = os.getenv("FINNHUB_API_KEY", "").strip()
        if api_key:
            self.finnhub = FinnhubProvider(api_key=api_key, symbol_map=symbol_map)
            logger.info(
                "[PROVIDER] Finnhub primary for: %s",
                ", ".join(sorted(symbol_map)) or "(none mapped)",
            )
        else:
            self.finnhub = None
            logger.info("[PROVIDER] FINNHUB_API_KEY not set — using yfinance only")

        self.yfinance = YFinanceProvider()

    def fetch_all(self) -> list[dict[str, Any]]:
        """
        Fetch quotes for all symbols. Returns a list of quote dicts
        (symbol, price, return_1m, return_5m, volume, source).
        """
        quotes: dict[str, dict[str, Any]] = {}

        if self.finnhub is not None:
            try:
                quotes.update(self.finnhub.fetch())
            except Exception as e:
                logger.error("[PROVIDER] Finnhub fetch failed: %s", e)

        remaining = [s for s in self._symbols if s not in quotes]
        if remaining:
            quotes.update(self.yfinance.fetch(remaining))

        per_source = {}
        for q in quotes.values():
            per_source.setdefault(q["source"], []).append(q["symbol"])
        for source, syms in per_source.items():
            logger.info("[PROVIDER] %s served %d symbols: %s", source, len(syms), ", ".join(syms))

        return [quotes[s] for s in self._symbols if s in quotes]
