#!/usr/bin/env python3
"""
Production-quality Kafka producer for real-time cross-asset market data.

Fetches quotes for 7 assets every 60 seconds via the multi-provider data
layer (Finnhub primary, Yahoo Finance fallback) and publishes structured
messages to Kafka. Includes exponential-backoff reconnection, delivery
callbacks, and graceful handling of market-closed hours.

Usage:
    python -m src.producers.market_data_producer
    KAFKA_BOOTSTRAP_SERVERS=kafka:29092 python -m src.producers.market_data_producer
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from kafka import KafkaProducer
from kafka.errors import KafkaError

from src.producers.data_provider import MarketDataProvider

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "market-data")

TICKERS = [
    "^GSPC",      # S&P 500
    "^IXIC",      # NASDAQ Composite
    "BTC-USD",    # Bitcoin
    "GC=F",       # Gold Futures
    "CL=F",       # Crude Oil Futures
    "EURUSD=X",   # EUR/USD
    "^TNX",       # 10-Year Treasury Yield
]

# Poll cadence. Yahoo's underlying feed does not update faster than this
# for any asset class, and going lower risks 429 rate limiting. Crypto/FX
# benefit from the faster refresh; delayed symbols are deduped anyway.
FETCH_INTERVAL_SECONDS = 30
MARKET_CLOSED_RETRY_SECONDS = 300
SHUTDOWN_TIMEOUT_SECONDS = 10

# Retry configuration for Kafka connection
RETRY_INITIAL_SECONDS = 5
RETRY_MAX_SECONDS = 60
RETRY_MULTIPLIER = 2

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("market_data_producer")


def setup_logging() -> None:
    """Configure structured logging for the producer."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Delivery Callbacks
# ---------------------------------------------------------------------------
# kafka-python reports per-message delivery via futures returned by send(),
# not via a constructor callback (that is confluent-kafka's API).

def on_delivery_success(record_metadata: Any) -> None:
    """Called when the broker acknowledges a produced message."""
    logger.debug(
        "[DELIVER-OK] topic=%s partition=%d offset=%d",
        record_metadata.topic,
        record_metadata.partition,
        record_metadata.offset,
    )


def on_delivery_error(excp: Any) -> None:
    """Called when delivery of a produced message permanently fails."""
    logger.error("[DELIVER-FAIL] %s", excp, exc_info=excp)


# ---------------------------------------------------------------------------
# Kafka Connection with Exponential Backoff
# ---------------------------------------------------------------------------

def create_kafka_producer() -> KafkaProducer:
    """
    Create a KafkaProducer, retrying with exponential backoff on failure.

    Starts at RETRY_INITIAL_SECONDS, doubles each attempt, caps at
    RETRY_MAX_SECONDS. Logs every attempt. Raises on exhausting retries
    beyond a reasonable total wall-clock time (15 minutes).
    """
    backoff = RETRY_INITIAL_SECONDS
    attempt = 0
    deadline = time.monotonic() + 900  # Hard stop after 15 minutes

    while time.monotonic() < deadline:
        attempt += 1
        logger.info(
            "[CONNECT] Attempt %d to %s (next retry in %ds if failed)",
            attempt,
            KAFKA_BOOTSTRAP_SERVERS,
            backoff,
        )
        try:
            producer = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                acks="all",
                retries=5,
                retry_backoff_ms=500,
                max_block_ms=30000,
                request_timeout_ms=30000,
                linger_ms=100,
                compression_type="gzip",
            )
            logger.info(
                "[CONNECT] Connected to Kafka at %s on attempt %d",
                KAFKA_BOOTSTRAP_SERVERS,
                attempt,
            )
            return producer

        except KafkaError as e:
            # Includes NoBrokersAvailable (kafka-python 2.x) and
            # KafkaConnectionError (kafka-python 3.x) on bootstrap failure
            logger.warning(
                "[CONNECT] Kafka error on attempt %d: %s — retrying in %ds",
                attempt,
                e,
                backoff,
            )
        except Exception as e:
            logger.warning(
                "[CONNECT] Unexpected error on attempt %d: %s — retrying in %ds",
                attempt,
                e,
                backoff,
            )

        time.sleep(backoff)
        backoff = min(backoff * RETRY_MULTIPLIER, RETRY_MAX_SECONDS)

    logger.error(
        "[CONNECT] Failed to connect to Kafka after %d attempts over %.0f minutes. Aborting.",
        attempt,
        (time.monotonic() - (deadline - 900)) / 60,
    )
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Data Fetching
# ---------------------------------------------------------------------------

def fetch_market_data(provider: MarketDataProvider) -> Optional[list[dict]]:
    """
    Fetch the latest quote for every configured ticker via the
    multi-provider layer (Finnhub primary, yfinance fallback).

    Returns a list of quote dicts, or None when no symbol returned data
    (all markets closed / network issue).
    """
    logger.info(
        "[FETCH] Fetching quotes for %d tickers: %s",
        len(TICKERS),
        ", ".join(TICKERS),
    )
    quotes = provider.fetch_all()

    if not quotes:
        logger.info(
            "[FETCH] No new data for any ticker "
            "(market closed or prices unchanged since last publish)"
        )
        return None

    return quotes


# ---------------------------------------------------------------------------
# Message Construction
# ---------------------------------------------------------------------------

def build_messages(
    quotes: list[dict],
    fetch_id: str,
) -> list[tuple[str, dict]]:
    """
    Build Kafka messages from provider quotes.

    Args:
        quotes: Quote dicts from the data provider (symbol, price,
                return_1m, return_5m, volume, source)
        fetch_id: UUID string shared by all messages in this fetch cycle

    Returns:
        List of (key, value) tuples ready for producer.send()
    """
    messages: list[tuple[str, dict]] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for quote in quotes:
        try:
            symbol = quote["symbol"]

            message = {
                "timestamp": now_iso,
                "symbol": symbol,
                "price": quote.get("price"),
                "return_1m": quote.get("return_1m"),
                "return_5m": quote.get("return_5m"),
                "volume": quote.get("volume"),
                "source": quote.get("source", "unknown"),
                "fetch_id": fetch_id,
            }

            messages.append((symbol, message))

            ret_1m = quote.get("return_1m")
            ret_1m_str = f"{ret_1m * 100:+.4f}%" if ret_1m is not None else "N/A"
            logger.info(
                "[PRODUCER] %s price=%.4f return_1m=%s (via %s) ready for kafka",
                symbol,
                quote.get("price", float("nan")),
                ret_1m_str,
                quote.get("source", "unknown"),
            )

        except Exception as e:
            logger.error(
                "[BUILD] Error building message for %s: %s",
                quote.get("symbol", "UNKNOWN"),
                e,
                exc_info=True,
            )

    return messages


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def publish_messages(
    producer: KafkaProducer,
    messages: list[tuple[str, dict]],
) -> int:
    """
    Publish messages to Kafka and flush.

    Returns the count of successfully enqueued messages.
    Per-message delivery is confirmed via future callbacks
    (on_delivery_success / on_delivery_error).
    """
    sent = 0
    for key, value in messages:
        try:
            future = producer.send(topic=KAFKA_TOPIC, key=key, value=value)
            future.add_callback(on_delivery_success)
            future.add_errback(on_delivery_error)
            sent += 1
        except KafkaError as e:
            logger.error(
                "[PUBLISH] Failed to enqueue message for %s: %s",
                value.get("symbol", "UNKNOWN"),
                e,
            )
        except Exception as e:
            logger.error(
                "[PUBLISH] Unexpected error enqueueing message for %s: %s",
                value.get("symbol", "UNKNOWN"),
                e,
            )

    # Flush to ensure all messages are delivered (or callbacks fire)
    try:
        producer.flush(timeout=30)
    except KafkaError as e:
        logger.error("[PUBLISH] Error during flush: %s", e)

    logger.info(
        "[PUBLISH] Batch complete: %d/%d messages enqueued to topic '%s'",
        sent,
        len(messages),
        KAFKA_TOPIC,
    )
    return sent


# ---------------------------------------------------------------------------
# Graceful Shutdown
# ---------------------------------------------------------------------------

class ShutdownHandler:
    """Coordinate graceful shutdown on SIGINT/SIGTERM."""

    def __init__(self):
        self._shutdown_requested = False
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        sig_name = signal.Signals(signum).name
        if self._shutdown_requested:
            logger.warning("[SHUTDOWN] Second %s received — forcing exit", sig_name)
            sys.exit(1)
        self._shutdown_requested = True
        logger.info("[SHUTDOWN] %s received, finishing current cycle ...", sig_name)

    @property
    def should_stop(self) -> bool:
        return self._shutdown_requested


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

def run() -> None:
    """
    Main producer loop.

    1. Connect to Kafka (with retry backoff)
    2. Every FETCH_INTERVAL_SECONDS, fetch quotes via the data provider
       layer (Finnhub primary, yfinance fallback)
    3. Build and publish messages
    4. If market is closed, sleep longer before retrying
    5. On shutdown signal, flush and close cleanly
    """
    setup_logging()
    shutdown = ShutdownHandler()

    logger.info("=" * 64)
    logger.info("  Market Data Producer — Starting")
    logger.info("  Kafka:   %s", KAFKA_BOOTSTRAP_SERVERS)
    logger.info("  Topic:   %s", KAFKA_TOPIC)
    logger.info("  Tickers: %s", ", ".join(TICKERS))
    logger.info("  Interval: %ds", FETCH_INTERVAL_SECONDS)
    logger.info("=" * 64)

    # Connect to Kafka
    producer = create_kafka_producer()

    # Data provider: Finnhub primary (if FINNHUB_API_KEY set), yfinance fallback
    provider = MarketDataProvider(TICKERS)

    # Track consecutive empty fetches to detect sustained market closure
    consecutive_empty = 0
    MAX_CONSECUTIVE_EMPTY = 3

    try:
        while not shutdown.should_stop:
            cycle_start = time.monotonic()
            fetch_id = str(uuid4())

            # --- Fetch ---
            quotes = fetch_market_data(provider)

            if not quotes:
                consecutive_empty += 1
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    logger.info(
                        "[MARKET-CLOSED] No data returned for %d consecutive fetches. "
                        "Market may be closed. Sleeping %d seconds before retrying.",
                        consecutive_empty,
                        MARKET_CLOSED_RETRY_SECONDS,
                    )
                    # Sleep in small increments so we can respond to shutdown signals
                    _interruptible_sleep(MARKET_CLOSED_RETRY_SECONDS, shutdown)
                    continue
                else:
                    logger.warning(
                        "[FETCH] Empty result (%d/%d consecutive), "
                        "retrying in %ds",
                        consecutive_empty,
                        MAX_CONSECUTIVE_EMPTY,
                        FETCH_INTERVAL_SECONDS,
                    )
                    _interruptible_sleep(FETCH_INTERVAL_SECONDS, shutdown)
                    continue

            # We got data — reset the empty counter
            consecutive_empty = 0

            # --- Build messages ---
            messages = build_messages(quotes, fetch_id)

            if not messages:
                logger.warning("[BUILD] No messages built from fetched data — skipping publish")
                _interruptible_sleep(FETCH_INTERVAL_SECONDS, shutdown)
                continue

            # --- Publish ---
            publish_messages(producer, messages)

            # --- Sleep for remaining interval ---
            elapsed = time.monotonic() - cycle_start
            remaining = max(0, FETCH_INTERVAL_SECONDS - elapsed)
            if remaining > 0:
                logger.debug("[SLEEP] Next fetch in %.1fs", remaining)
                _interruptible_sleep(remaining, shutdown)

    except KeyboardInterrupt:
        logger.info("[SHUTDOWN] Keyboard interrupt received")
    except Exception as e:
        logger.critical("[FATAL] Unhandled exception in main loop: %s", e, exc_info=True)
        raise
    finally:
        logger.info("[SHUTDOWN] Flushing remaining messages (timeout=%ds) ...", SHUTDOWN_TIMEOUT_SECONDS)
        try:
            producer.flush(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except Exception:
            pass
        logger.info("[SHUTDOWN] Closing Kafka producer ...")
        try:
            producer.close(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except Exception:
            pass
        logger.info("[SHUTDOWN] Producer stopped cleanly")


def _interruptible_sleep(seconds: float, shutdown: ShutdownHandler) -> None:
    """
    Sleep in 1-second increments so shutdown signals are honored promptly.

    Without this, a 5-minute market-closed sleep would block SIGINT handling.
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if shutdown.should_stop:
            return
        time.sleep(min(1.0, end - time.monotonic()))


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run()