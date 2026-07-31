import asyncio
import os
import uuid

import pytest
from dms_eventbus_client import Event, NatsEventBusClient
from fastapi.testclient import TestClient
from permission_service.main import app
from permission_service.settings import ROOT_RESOURCE_ID

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


async def _collect_one(consumer, subject: str):
    received: list[Event] = []
    got_message = asyncio.Event()

    async def handler(payload: bytes) -> None:
        received.append(Event.from_bytes(payload))
        got_message.set()

    await consumer.subscribe(
        subject, handler, durable=f"test-{uuid.uuid4().hex[:8]}", deliver_new=True
    )
    return received, got_message


async def test_create_scope_lock_publishes_event(client, consumer):
    received, got_message = await _collect_one(consumer, "permission.scope_lock.created")

    response = client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin", "reason": "Migration"},
    )
    lock_id = response.json()["scope_lock"]["id"]

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].payload == {
        "scope_lock_id": lock_id,
        "resource_id": ROOT_RESOURCE_ID,
        "locked_by": "admin",
        "reason": "Migration",
        "blocks_read": False,
    }


async def test_release_scope_lock_publishes_event(client, consumer):
    lock = client.post(
        "/scope-locks", json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"}
    ).json()["scope_lock"]

    received, got_message = await _collect_one(consumer, "permission.scope_lock.released")

    client.request("DELETE", f"/scope-locks/{lock['id']}", json={"released_by": "admin2"})

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].payload == {
        "scope_lock_id": lock["id"],
        "resource_id": ROOT_RESOURCE_ID,
        "released_by": "admin2",
    }
