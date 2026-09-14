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


async def _publish(event_type: str, payload: dict) -> None:
    producer = NatsEventBusClient(NATS_URL, stream="folder")
    await producer.connect()
    try:
        event = Event(event_type=event_type, service_name="folder-service-sim", payload=payload)
        await producer.publish(event_type, event.to_bytes())
    finally:
        await producer.close()


async def _poll_until(predicate, timeout_seconds=5.0, interval=0.1):
    elapsed = 0.0
    while elapsed < timeout_seconds:
        if predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


def test_resource_created_event_creates_node(client):
    resource_id = f"folder-{uuid.uuid4().hex[:8]}"
    asyncio.run(
        _publish(
            "folder.resource.created", {"resource_id": resource_id, "parent_id": ROOT_RESOURCE_ID}
        )
    )

    found = asyncio.run(
        _poll_until(lambda: client.get(f"/resources/{resource_id}").status_code == 200)
    )
    assert found, "Ressource wurde nicht rechtzeitig über das Event angelegt"

    body = client.get(f"/resources/{resource_id}").json()
    assert body["parent_id"] == ROOT_RESOURCE_ID


# --- POST /resources (Post-Roadmap Phase 35 Session 2, ADR 0144) - the
# synchronous counterpart to the event-driven creation above, added
# specifically so `case-service`'s own per-case checks don't have to wait
# for NATS propagation before the resource they depend on exists. Shares
# `repository.create_resource_node` with the event handler above, so these
# tests focus on the REST-specific behavior (immediate consistency, no
# polling needed) rather than re-testing the shared create-if-missing logic
# itself. -----------------------------------------------------------------


def test_post_resources_synchronously_creates_node_no_polling_needed(client):
    resource_id = f"case-{uuid.uuid4().hex[:8]}"

    created = client.post(
        "/resources",
        json={"resource_id": resource_id, "parent_id": ROOT_RESOURCE_ID, "resource_type": "case"},
    )

    assert created.status_code == 201
    assert created.json()["parent_id"] == ROOT_RESOURCE_ID
    assert created.json()["resource_type"] == "case"
    # No `_poll_until` here on purpose - the whole point of this endpoint
    # over the event-driven path is that the row already exists by the time
    # the response returns.
    fetched = client.get(f"/resources/{resource_id}")
    assert fetched.status_code == 200
    assert fetched.json()["resource_type"] == "case"


def test_post_resources_is_idempotent_and_never_overwrites_an_existing_node(client):
    resource_id = f"case-{uuid.uuid4().hex[:8]}"
    other_parent = f"case-{uuid.uuid4().hex[:8]}"
    client.post(
        "/resources",
        json={"resource_id": other_parent, "parent_id": ROOT_RESOURCE_ID, "resource_type": "case"},
    )
    client.post(
        "/resources",
        json={"resource_id": resource_id, "parent_id": ROOT_RESOURCE_ID, "resource_type": "case"},
    )

    second_call = client.post(
        "/resources",
        json={"resource_id": resource_id, "parent_id": other_parent, "resource_type": "folder"},
    )

    assert second_call.status_code == 201
    # The already-existing node's own parent_id/resource_type win - a
    # second create-if-missing call is a harmless no-op, never a silent
    # overwrite (same semantics as the event handler's own prior "if
    # missing, insert" behavior).
    assert second_call.json()["parent_id"] == ROOT_RESOURCE_ID
    assert second_call.json()["resource_type"] == "case"


def test_resource_moved_event_updates_parent(client):
    a = f"folder-{uuid.uuid4().hex[:8]}"
    b = f"folder-{uuid.uuid4().hex[:8]}"
    asyncio.run(
        _publish("folder.resource.created", {"resource_id": a, "parent_id": ROOT_RESOURCE_ID})
    )
    asyncio.run(
        _publish("folder.resource.created", {"resource_id": b, "parent_id": ROOT_RESOURCE_ID})
    )
    asyncio.run(_poll_until(lambda: client.get(f"/resources/{b}").status_code == 200))

    asyncio.run(_publish("folder.resource.moved", {"resource_id": a, "new_parent_id": b}))

    found = asyncio.run(
        _poll_until(lambda: client.get(f"/resources/{a}").json().get("parent_id") == b)
    )
    assert found, "Verschieben wurde nicht rechtzeitig übernommen"


def test_resource_deleted_event_removes_node(client):
    resource_id = f"folder-{uuid.uuid4().hex[:8]}"
    asyncio.run(
        _publish(
            "folder.resource.created", {"resource_id": resource_id, "parent_id": ROOT_RESOURCE_ID}
        )
    )
    asyncio.run(_poll_until(lambda: client.get(f"/resources/{resource_id}").status_code == 200))

    asyncio.run(_publish("folder.resource.deleted", {"resource_id": resource_id}))

    removed = asyncio.run(
        _poll_until(lambda: client.get(f"/resources/{resource_id}").status_code == 404)
    )
    assert removed, "Löschung wurde nicht rechtzeitig übernommen"
