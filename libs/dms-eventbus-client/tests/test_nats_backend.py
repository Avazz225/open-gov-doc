import asyncio
import os
import uuid

import pytest
from dms_eventbus_client import NatsEventBusClient

NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")


@pytest.fixture
async def client():
    stream = f"test_{uuid.uuid4().hex[:8]}"
    bus = NatsEventBusClient(NATS_URL, stream)
    await bus.connect()
    yield bus, stream
    await bus.close()


async def test_publish_subscribe_roundtrip(client):
    bus, stream = client
    received: list[bytes] = []
    got_message = asyncio.Event()

    async def handler(payload: bytes) -> None:
        received.append(payload)
        got_message.set()

    await bus.subscribe(f"{stream}.ping", handler, durable="test-consumer")
    await asyncio.sleep(0.2)  # Subscription muss beim Server aktiv sein, bevor publiziert wird
    await bus.publish(f"{stream}.ping", b"hello")

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received == [b"hello"]


async def test_pure_consumer_without_stream_ownership():
    """Ein Konsument wie der Audit Service besitzt keinen eigenen Stream (ADR 0001) -
    er abonniert das Subject eines fremden, bereits existierenden Streams direkt.
    """
    stream = f"test_{uuid.uuid4().hex[:8]}"
    producer = NatsEventBusClient(NATS_URL, stream)
    await producer.connect()

    consumer = NatsEventBusClient(NATS_URL, ensure_stream=False)
    await consumer.connect()

    received: list[bytes] = []
    got_message = asyncio.Event()

    async def handler(payload: bytes) -> None:
        received.append(payload)
        got_message.set()

    try:
        await consumer.subscribe(f"{stream}.created", handler, durable="consumer-test")
        await asyncio.sleep(0.2)
        await producer.publish(f"{stream}.created", b"payload")

        await asyncio.wait_for(got_message.wait(), timeout=5)
        assert received == [b"payload"]
    finally:
        await producer.close()
        await consumer.close()


def test_ensure_stream_true_requires_stream_name():
    with pytest.raises(ValueError, match="stream ist erforderlich"):
        NatsEventBusClient(NATS_URL, stream=None, ensure_stream=True)
