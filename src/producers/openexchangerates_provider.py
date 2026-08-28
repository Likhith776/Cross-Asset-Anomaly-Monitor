"""
OpenExchangeRates — keyless daily FX rates for any cloud network.

Public endpoint at open.er-api.com serves rates against USD without
authentication, updated daily, and unlike Yahoo it doesn't anti-bot
datacenter IPs. The catch: rates are end-of-day, so this is a coarse
daily fill rather than minute-by-minute — perfect for the dashboard's
EURUSD=X tile where freshness is fuzzy anyway.

Output contract matches the other providers: dicts with
{symbol, price, return_1m, return_5m, volume, source, timestamp}.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

OPENEXCHANGERATES_URL = "https://open.er-api.com/v6/latest/USD"
TIMEOUT_SECONDS = 10
USER_AGENT = "cross-asset-monitor/1.0"

# Tracked FX pairs expressed as the inverse of the USD base, so we
# quote them in the same convention the existing yfinance path uses.
SYMBOL_TO_BASE = {"EURUSD=X": "EUR"}


class OpenExchangeRatesProvider:
    def __init__(self, symbols):
        self._symbols = [s for s in symbols if s in SYMBOL_TO_BASE]

    @property
    def symbols(self):
        return list(self._symbols)

    def _fetch_rates(self) -> dict[str, float]:
        resp = requests.get(
            OPENEXCHANGERATES_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            logger.warning("[OXR] HTTP %d", resp.status_code)
            return {}
        payload = resp.json()
        if payload.get("result") != "success":
            logger.warning("[OXR] non-success payload: %s", payload.get("result"))
            return {}
        return payload.get("rates", {})

    def fetch_all(self) -> list[dict[str, Any]]:
        rates = self._fetch_rates()
        if not rates:
            return []
        ts = (
            datetime.fromtimestamp(
                rates.get("time_last_update_unix", 0), timezone.utc
            ).isoformat()
            if rates.get("time_last_update_unix")
            else datetime.now(timezone.utc).isoformat()
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        quotes = []
        for symbol in self._symbols:
            base = SYMBOL_TO_BASE[symbol]
            rate = rates.get(base)
            if rate is None or rate <= 0:
                continue
            quotes.append({
                "symbol": symbol,
                "price": round(1.0 / float(rate), 6),
                "return_1m": None,
                "return_5m": None,
                "volume": None,
                "source": "openexchangerates",
                "timestamp": ts or now_iso,
            })
        return quotes
