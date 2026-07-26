import asyncio
import os
import uuid

import pytest
from dms_eventbus_client import Event, NatsEventBusClient
from document_service.main import app
from fastapi.testclient import TestClient

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


async def _collect_one(consumer, subject: str) -> list[Event]:
    received: list[Event] = []
    got_message = asyncio.Event()

    async def handler(payload: bytes) -> None:
        received.append(Event.from_bytes(payload))
        got_message.set()

    await consumer.subscribe(
        subject, handler, durable=f"test-{uuid.uuid4().hex[:8]}", deliver_new=True
    )
    return received, got_message


async def test_create_document_publishes_event(client, consumer):
    received, got_message = await _collect_one(consumer, "document.created")

    response = client.post(
        "/documents",
        data={"title": "Vertrag", "created_by": "alice"},
        files={"file": ("vertrag.pdf", b"Inhalt", "application/pdf")},
    )
    document_id = response.json()["id"]

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].subject == document_id
    assert received[0].payload["created_by"] == "alice"


async def test_force_release_publishes_event(client, consumer):
    upload = client.post(
        "/documents",
        data={"title": "Vertrag", "created_by": "alice"},
        files={"file": ("vertrag.pdf", b"Inhalt", "application/pdf")},
    )
    document_id = upload.json()["id"]
    client.post(f"/documents/{document_id}/lock", json={"locked_by": "alice", "session_id": "s1"})

    received, got_message = await _collect_one(consumer, "document.lock.force_released")

    client.post(
        f"/documents/{document_id}/lock/force-release",
        json={"released_by": "admin", "reason": "Test"},
    )

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].payload["original_locked_by"] == "alice"
    assert received[0].payload["released_by"] == "admin"
