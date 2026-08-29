"""
Base consumer class providing the interface and common logic
for all Kafka consumers in the system.
"""

import os
import json
import logging
import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from aiokafka import AIOKafkaConsumer

logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "market-data")
CONSUMER_GROUP = os.getenv("CONSUMER_GROUP", "anomaly-detection-group")


class BaseConsumer(ABC):
    """Abstract base class for Kafka consumers."""

    def __init__(
        self,
        bootstrap_servers: Optional[str] = None,
        topic: Optional[str] = None,
        group_id: Optional[str] = None,
    ):
        self.bootstrap_servers = bootstrap_servers or KAFKA_BOOTSTRAP_SERVERS
        self.topic = topic or KAFKA_TOPIC
        self.group_id = group_id or CONSUMER_GROUP
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._running = False

    async def start(self) -> None:
        """Initialize and start the Kafka consumer."""
        self._consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            key_deserializer=lambda k: k.decode("utf-8") if k else None,
            auto_offset_reset="latest",
            enable_auto_commit=False,
            max_poll_records=500,
            session_timeout_ms=30000,
            heartbeat_interval_ms=10000,
        )
        await self._consumer.start()
        self._running = True
        logger.info(
            "Consumer started: bootstrap_servers=%s, topic=%s, group=%s",
            self.bootstrap_servers,
            self.topic,
            self.group_id,
        )

    async def stop(self) -> None:
        """Stop the Kafka consumer."""
        self._running = False
        if self._consumer:
            await self._consumer.stop()
            logger.info("Consumer stopped")

    @abstractmethod
    async def process_message(self, key: Optional[str], value: dict[str, Any]) -> None:
        """Process a single consumed message. Must be implemented by subclasses."""
        ...

    async def run(self) -> None:
        """Consume messages in a loop and process each one."""
        await self.start()

        from src.metrics import (
            CONSUMER_LAG,
            MESSAGES_CONSUMED,
            start_metrics_server_if_configured,
        )

        start_metrics_server_if_configured()

        async def _sample_lag() -> None:
            """Periodic consumer-lag sampler; failures never break the loop."""
            while True:
                await asyncio.sleep(60)
                try:
                    partitions = self._consumer.assignment()
                    if not partitions:
                        continue
                    end_offsets = await self._consumer.end_offsets(partitions)
                    for tp in partitions:
                        committed = await self._consumer.committed(tp)
                        if committed is None:
                            continue
                        lag = end_offsets.get(tp, 0) - committed.offset
                        CONSUMER_LAG.labels(f"{tp.topic}:{tp.partition}").set(lag)
                except Exception as exc:
                    logger.debug("[LAG] sampling failed: %s", exc)

        lag_task = asyncio.create_task(_sample_lag())

        try:
            async for message in self._consumer:
                if not self._running:
                    break
                try:
                    MESSAGES_CONSUMED.inc()
                    await self.process_message(key=message.key, value=message.value)
                    await self._consumer.commit()
                except Exception as e:
                    logger.error(
                        "Error processing message (key=%s, offset=%d): %s",
                        message.key,
                        message.offset,
                        e,
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            logger.info("Consumer run loop cancelled")
        finally:
            lag_task.cancel()
            await self.stop()