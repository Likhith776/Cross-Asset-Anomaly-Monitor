"""
Coinbase spot price provider — keyless, works from US cloud runners.

Binance geo-blocks US IPs with HTTP 451, which is exactly where GitHub
Actions runs; Coinbase (a US exchange) serves its public spot endpoint
without keys or restrictions. Crypto symbols only; other symbols are
ignored so a composite provider can layer more sources beside it.

Output contract matches the other providers: dicts with
{symbol, price, return_1m, return_5m, volume, source, timestamp}.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

COINBASE_SPOT_URL = "https://api.coinbase.com/v2/prices/{pair}/spot"
COINBASE_TIMEOUT_SECONDS = 10
USER_AGENT = "cross-asset-monitor/1.0"

COINBASE_SYMBOL_MAP = {
    "BTC-USD": "BTC-USD",
}


class CoinbaseProvider:
    def __init__(self, symbols):
        self._symbols = [s for s in symbols if s in COINBASE_SYMBOL_MAP]

    @property
    def symbols(self):
        return list(self._symbols)

    def _fetch_one(self, pair: str) -> float | None:
        resp = requests.get(
            COINBASE_SPOT_URL.format(pair=pair),
            headers={"User-Agent": USER_AGENT},
            timeout=COINBASE_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            logger.warning("[COINBASE] %s -> HTTP %d", pair, resp.status_code)
            return None
        return float(resp.json()["data"]["amount"])

    def fetch_all(self) -> list[dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        quotes = []
        for symbol in self._symbols:
            price = self._fetch_one(COINBASE_SYMBOL_MAP[symbol])
            if price is None or price <= 0:
                continue
            quotes.append({
                "symbol": symbol,
                "price": round(price, 4),
                "return_1m": None,
                "return_5m": None,
                "volume": None,
                "source": "coinbase",
                "timestamp": now_iso,
            })
        return quotes
