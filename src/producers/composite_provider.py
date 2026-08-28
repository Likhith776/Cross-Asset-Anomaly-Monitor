"""
Composite quote provider: guaranteed primary sources + best-effort fill.

Built for CI hosting where the secondary source (Yahoo, via yfinance)
is intermittently blocked on datacenter IPs. Primaries run first and
always win; the secondary is retried until every still-missing symbol
has a quote or attempts are exhausted. Missing coverage is visible to
callers as absent symbols (the dashboard's freshness badges surface it).
"""

import logging
import time

logger = logging.getLogger(__name__)


class CompositeProvider:
    """
    primaries:  providers consulted in order; their quotes always win.
    secondary:  fallback for symbols no primary covered.
    attempts:   how many times to retry the secondary while symbols are
                still missing (each attempt costs one full fetch pass).
    retry_wait: seconds between secondary attempts.
    """

    def __init__(self, symbols, primaries, secondary,
                 attempts: int = 2, retry_wait: int = 20):
        self._symbols = list(symbols)
        self.primaries = list(primaries)
        self.secondary = secondary
        self.attempts = max(1, attempts)
        self.retry_wait = retry_wait

    def fetch_all(self) -> list[dict]:
        by_symbol: dict[str, dict] = {}
        wanted = set(self._symbols)

        for primary in self.primaries:
            try:
                for quote in primary.fetch_all():
                    by_symbol.setdefault(quote["symbol"], quote)
            except Exception:
                logger.exception(
                    "[COMPOSITE] primary %s failed", type(primary).__name__
                )

        for attempt in range(1, self.attempts + 1):
            missing = wanted - set(by_symbol)
            if not missing:
                break
            try:
                for quote in self.secondary.fetch_all():
                    by_symbol.setdefault(quote["symbol"], quote)
            except Exception:
                logger.exception("[COMPOSITE] secondary failed")

            still_missing = wanted - set(by_symbol)
            if not still_missing:
                break
            if attempt < self.attempts:
                logger.info(
                    "[COMPOSITE] %d symbols still missing after attempt %d: %s",
                    len(still_missing), attempt, ", ".join(sorted(still_missing)),
                )
                time.sleep(self.retry_wait)

        covered = sorted(set(by_symbol) & wanted)
        logger.info(
            "[COMPOSITE] coverage: %d/%d symbols (%s)",
            len(covered), len(wanted), ", ".join(covered),
        )
        return list(by_symbol.values())
