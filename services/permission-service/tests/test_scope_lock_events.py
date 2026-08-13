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


def _grant_scope_lock_permission(client, principal_id: str) -> None:
    """Self-Gating (Post-Roadmap Phase 19 Session 6, ADR 0071): `POST`/
    `DELETE /scope-locks` verlangen seither `admin.user_management` für den
    im Body übergebenen `locked_by`/`released_by`. Weist die bereits
    vorgeseedete "domain-admin-users"-Rolle zu, analog zu
    `test_api.py`s `role_management_headers`."""
    roles = client.get("/roles").json()
    role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-users")
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": principal_id,
            "role_id": role_id,
            "resource_id": ROOT_RESOURCE_ID,
        },
    )


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
    _grant_scope_lock_permission(client, "admin")
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
    _grant_scope_lock_permission(client, "admin")
    _grant_scope_lock_permission(client, "admin2")
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
