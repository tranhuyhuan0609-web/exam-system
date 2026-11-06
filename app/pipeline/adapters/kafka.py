"""Kafka adapters (aiokafka): a JSON producer and a consumer loop with manual commits and a dead-letter hop."""

import asyncio
import json
import logging
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from app.config import get_settings
from app.pipeline.ports import DLQ_TOPIC, LIBRARY_TOPIC, RAW_TOPIC

log = logging.getLogger(__name__)


async def ensure_topics(bootstrap: str | None = None, partitions: int = 3) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap or get_settings().kafka_bootstrap)
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        wanted = [
            NewTopic(name, num_partitions=partitions, replication_factor=1)
            for name in (RAW_TOPIC, DLQ_TOPIC, LIBRARY_TOPIC)
            if name not in existing
        ]
        if wanted:
            try:
                await admin.create_topics(wanted)
            except TopicAlreadyExistsError:
                pass
    finally:
        await admin.close()


class KafkaBus:
    """EventBus on Kafka. Messages are small JSON documents (ids, not payloads)."""

    def __init__(self, bootstrap: str | None = None):
        self._bootstrap = bootstrap or get_settings().kafka_bootstrap
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> "KafkaBus":
        if self._producer is None:
            await ensure_topics(self._bootstrap)
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap,
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False, default=str).encode(),
                key_serializer=lambda k: k.encode() if k else None,
                acks="all",
                enable_idempotence=True,
            )
            await self._producer.start()
        return self

    async def publish(self, topic: str, value: dict[str, Any], key: str | None = None) -> None:
        await self.start()
        await self._producer.send_and_wait(topic, value, key=key)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None


Handler = Callable[[dict[str, Any]], Awaitable[None]]


async def consume(
    topic: str,
    group: str,
    handler: Handler,
    on_error: Callable[[dict[str, Any], str], Awaitable[None]] | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Process messages one by one; a failing message goes to `on_error` (the DLQ) and the offset still
    advances, so one poison pill never blocks the partition."""
    settings = get_settings()
    await ensure_topics(settings.kafka_bootstrap)
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        value_deserializer=lambda b: json.loads(b.decode()),
        max_poll_interval_ms=10 * 60 * 1000,
    )
    await consumer.start()
    log.info("Consuming %s as %s", topic, group)
    try:
        while not (stop and stop.is_set()):
            batches = await consumer.getmany(timeout_ms=1000, max_records=10)
            for _partition, messages in batches.items():
                for message in messages:
                    try:
                        await handler(message.value)
                    except Exception:  # noqa: BLE001 - any failure becomes a dead letter
                        error = traceback.format_exc()
                        log.exception("Failed on %s", message.value)
                        if on_error:
                            await on_error(message.value, error)
                    await consumer.commit()
    finally:
        await consumer.stop()
