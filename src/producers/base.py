"""
Base producer class providing the interface and common logic
for all market data producers.
"""

import os
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Generator, Optional

from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "market-data")


class BaseProducer(ABC):
    """Abstract base class for market data producers."""

    def __init__(
        self,
        bootstrap_servers: Optional[str] = None,
        topic: Optional[str] = None,
    ):
        self.bootstrap_servers = bootstrap_servers or KAFKA_BOOTSTRAP_SERVERS
        self.topic = topic or KAFKA_TOPIC
        self._producer: Optional[AIOKafkaProducer] = None
        self._running = False

    async def start(self) -> None:
        """Initialize and start the Kafka producer."""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            acks="all",
            enable_idempotence=True,
            max_request_size=1048576,
            linger_ms=10,
        )
        await self._producer.start()
        self._running = True
        logger.info(
            "Producer started: bootstrap_servers=%s, topic=%s",
            self.bootstrap_servers,
            self.topic,
        )

    async def stop(self) -> None:
        """Stop the Kafka producer."""
        self._running = False
        if self._producer:
            await self._producer.stop()
            logger.info("Producer stopped")

    async def publish(self, key: str, value: dict[str, Any]) -> None:
        """Publish a single message to the Kafka topic."""
        if not self._producer or not self._running:
            raise RuntimeError("Producer is not started")
        value["produced_at"] = datetime.now(timezone.utc).isoformat()
        await self._producer.send_and_wait(self.topic, key=key, value=value)
        logger.debug("Published message: key=%s, asset=%s", key, value.get("asset"))

    async def publish_batch(self, records: list[tuple[str, dict[str, Any]]]) -> int:
        """Publish a batch of messages. Returns count of successfully sent."""
        if not self._producer or not self._running:
            raise RuntimeError("Producer is not started")
        count = 0
        for key, value in records:
            value["produced_at"] = datetime.now(timezone.utc).isoformat()
            await self._producer.send(self.topic, key=key, value=value)
            count += 1
        await self._producer.flush()
        logger.info("Published batch of %d messages", count)
        return count

    @abstractmethod
    def generate(self) -> Generator[dict[str, Any], None, None]:
        """Generate market data records. Must be implemented by subclasses."""
        ...

    async def run(self, interval_seconds: float = 1.0) -> None:
        """Continuously generate and publish market data."""
        import asyncio

        await self.start()
        try:
            while self._running:
                for record in self.generate():
                    asset = record.get("asset", "UNKNOWN")
                    await self.publish(key=asset, value=record)
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Producer run loop cancelled")
        finally:
            await self.stop()