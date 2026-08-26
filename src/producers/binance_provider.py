"""
Binance spot price provider — keyless, reliable from any network.

Public market-data endpoint, no authentication, generous rate limits,
and unlike Yahoo it serves cloud/CI IP ranges without anti-bot walls.
Maps crypto symbols only (BTC-USD today); other symbols are ignored so
a composite provider can layer additional sources beside it.

Output contract matches YFinanceProvider.fetch(): dicts with
{symbol, price, return_1m, return_5m, volume, source, timestamp}.
"""

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"
BINANCE_TIMEOUT_SECONDS = 10
USER_AGENT = "cross-asset-monitor/1.0"

BINANCE_SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT",
}


class BinanceProvider:
    """Real-time crypto quotes for the subset of symbols Binance maps."""

    def __init__(self, symbols):
        self._symbols = [s for s in symbols if s in BINANCE_SYMBOL_MAP]

    @property
    def symbols(self):
        return list(self._symbols)

    def _fetch_one(self, pair: str) -> float | None:
        resp = requests.get(
            BINANCE_TICKER_URL,
            params={"symbol": pair},
            headers={"User-Agent": USER_AGENT},
            timeout=BINANCE_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            logger.warning("[BINANCE] %s -> HTTP %d", pair, resp.status_code)
            return None
        return float(resp.json()["price"])

    def fetch_all(self) -> list[dict[str, Any]]:
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        quotes = []
        for symbol in self._symbols:
            price = self._fetch_one(BINANCE_SYMBOL_MAP[symbol])
            if price is None or price <= 0:
                continue
            quotes.append({
                "symbol": symbol,
                "price": round(price, 4),
                "return_1m": None,
                "return_5m": None,
                "volume": None,
                "source": "binance",
                "timestamp": now_iso,
            })
        return quotes
