import asyncio
import os
import uuid

import pytest
from dms_eventbus_client import Event, NatsEventBusClient
from fastapi.testclient import TestClient
from registry_service.main import app

NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def consumer():
    bus = NatsEventBusClient(NATS_URL, ensure_stream=False)
    await bus.connect()
    yield bus
    await bus.close()


async def test_register_publishes_event(client, consumer):
    received: list[Event] = []
    got_message = asyncio.Event()

    async def handler(payload: bytes) -> None:
        received.append(Event.from_bytes(payload))
        got_message.set()

    await consumer.subscribe(
        "registry.instance.registered",
        handler,
        durable=f"test-{uuid.uuid4().hex[:8]}",
        deliver_new=True,
    )
    await asyncio.sleep(0.2)

    instance_id = f"test-{uuid.uuid4().hex[:8]}"
    client.post(
        "/instances",
        json={
            "instance_id": instance_id,
            "service_type": "document-service",
            "version": "0.1.0",
            "capabilities": [],
            "health_endpoint": "http://doc-1:8000/healthz",
            "address": "http://doc-1:8000",
        },
    )

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].subject == instance_id
    assert received[0].service_name == "registry-service"
    assert received[0].payload["service_type"] == "document-service"


async def test_deregister_publishes_event(client, consumer):
    instance_id = f"test-{uuid.uuid4().hex[:8]}"
    client.post(
        "/instances",
        json={
            "instance_id": instance_id,
            "service_type": "document-service",
            "version": "0.1.0",
            "capabilities": [],
            "health_endpoint": "http://doc-1:8000/healthz",
            "address": "http://doc-1:8000",
        },
    )

    received: list[Event] = []
    got_message = asyncio.Event()

    async def handler(payload: bytes) -> None:
        received.append(Event.from_bytes(payload))
        got_message.set()

    await consumer.subscribe(
        "registry.instance.deregistered",
        handler,
        durable=f"test-{uuid.uuid4().hex[:8]}",
        deliver_new=True,
    )
    await asyncio.sleep(0.2)

    client.delete(f"/instances/{instance_id}")

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].subject == instance_id
