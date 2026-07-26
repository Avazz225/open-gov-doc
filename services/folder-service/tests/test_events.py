import asyncio
import os
import uuid

import pytest
from dms_eventbus_client import Event, NatsEventBusClient
from fastapi.testclient import TestClient
from folder_service.main import app

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


async def test_create_publishes_resource_created_matching_permission_service_contract(
    client, consumer
):
    received, got_message = await _collect_one(consumer, "folder.resource.created")

    response = client.post("/folders", json={"name": "Projekte", "created_by": "alice"})
    folder_id = response.json()["id"]

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].payload == {
        "resource_id": folder_id,
        "parent_id": "root",
        "resource_type": "folder",
    }


async def test_move_publishes_resource_moved(client, consumer):
    parent_a = client.post("/folders", json={"name": "A", "created_by": "alice"}).json()
    parent_b = client.post("/folders", json={"name": "B", "created_by": "alice"}).json()
    child = client.post(
        "/folders", json={"name": "Kind", "parent_id": parent_a["id"], "created_by": "alice"}
    ).json()

    received, got_message = await _collect_one(consumer, "folder.resource.moved")

    client.patch(f"/folders/{child['id']}", json={"parent_id": parent_b["id"]})

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].payload == {"resource_id": child["id"], "new_parent_id": parent_b["id"]}


async def test_delete_publishes_resource_deleted(client, consumer):
    created = client.post("/folders", json={"name": "Leer", "created_by": "alice"}).json()

    received, got_message = await _collect_one(consumer, "folder.resource.deleted")

    client.delete(f"/folders/{created['id']}")

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].payload == {"resource_id": created["id"]}
