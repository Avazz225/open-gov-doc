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
    """Post-Roadmap Phase 38 Session 4 (ADR 0149): default `X-DMS-Principal`,
    same pattern as `test_api.py`'s `client` fixture - core folder CRUD now
    requires a valid principal."""
    with TestClient(app, headers={"X-DMS-Principal": "folder-service-tests"}) as c:
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


async def test_manual_purge_publishes_resource_deleted(client, consumer):
    """Post-Roadmap Phase 44 Session 2 (ADR 0163) - `DELETE /folders/{id}`
    (tested above) was the only hard-delete path that ever published this
    structure-tree event; `POST /folders/{id}/purge` (this test), the
    retention-poll-driven trash-expiry purge, the retention-poll-driven
    forced deletion (see below), and `reconcile-restore-deletion` all
    shared the same, previously-undetected gap - only the `folder.trash_
    purged`/`folder.force_deleted` BUSINESS events were published, never
    `folder.resource.deleted`, leaving orphaned `ResourceNode` rows in
    `permission-service` on every one of these paths (ADR 0154's own
    documented, then-untracked finding)."""
    created = client.post("/folders", json={"name": "Weg", "created_by": "alice"}).json()
    client.post(f"/folders/{created['id']}/trash", json={"deleted_by": "alice"})

    received, got_message = await _collect_one(consumer, "folder.resource.deleted")

    # `admin.deletion`-gated (ADR 0150), not the legacy `X-DMS-Roles` check
    # `reconcile-restore-deletion` below still uses - see `conftest.py`'s
    # `DELETION_ADMIN_PRINCIPAL_ID`/its autouse role-grant fixture.
    response = client.post(
        f"/folders/{created['id']}/purge",
        headers={"X-DMS-Principal": "folder-service-test-deletion-admin"},
    )
    assert response.status_code == 204

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].payload == {"resource_id": created["id"]}


async def test_reconcile_restore_deletion_publishes_resource_deleted(client, consumer):
    created = client.post("/folders", json={"name": "Weg", "created_by": "alice"}).json()

    received, got_message = await _collect_one(consumer, "folder.resource.deleted")

    response = client.post(
        f"/folders/{created['id']}/reconcile-restore-deletion",
        json={"original_entry_id": "led-1", "reason": "Restore-Abgleich"},
        headers={"X-DMS-Roles": "dms-admin"},
    )
    assert response.status_code == 204

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].payload == {"resource_id": created["id"]}


async def test_execute_or_defer_forced_deletion_publishes_resource_deleted(session, consumer):
    """The retention-poll-driven forced-deletion path
    (`main._execute_or_defer_forced_deletion`, called from
    `_retention_poll_loop`) - called directly here, same established
    pattern as `document_service.tests.test_retention_actions`'s own
    direct calls to its counterpart (triggering it via a real poll tick
    would need manipulating `retention_until`/waiting for the loop's own
    interval). Deliberately uses the bare `session` fixture, NOT the
    `client` fixture above - `TestClient`'s lifespan runs the app in its
    own anyio portal/event loop, and a session obtained from `app.state.
    session_factory` inside THIS test's own event loop hit a genuine
    cross-event-loop `RuntimeError` from asyncpg when tried (same reason
    `document_service`'s own precedent test never uses `TestClient`
    either - see its docstring). `app.state.approval_client`/
    `document_client` need the same manual stub-and-restore that
    precedent uses, since no lifespan ever ran to set them; `event_bus`
    is stubbed with a REAL `NatsEventBusClient`, not a mock, so the
    actual publish is genuinely observed by `consumer` below."""
    from unittest.mock import AsyncMock

    from dms_eventbus_client import NatsEventBusClient
    from folder_service import main, repository

    folder = await repository.create_folder(
        session,
        name="Zwangslöschung",
        parent_id="root",
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    await session.commit()

    fake_approval_client = AsyncMock()
    fake_approval_client.requires_approval.return_value = False
    fake_document_client = AsyncMock()
    fake_document_client.count_active.return_value = 0
    event_bus = NatsEventBusClient(NATS_URL, ensure_stream=False)
    await event_bus.connect()

    def _swap(name, value):
        had = hasattr(main.app.state, name)
        original = getattr(main.app.state, name, None)
        setattr(main.app.state, name, value)
        return had, original

    swaps = [
        ("approval_client", *_swap("approval_client", fake_approval_client)),
        ("document_client", *_swap("document_client", fake_document_client)),
        ("event_bus", *_swap("event_bus", event_bus)),
    ]
    try:
        received, got_message = await _collect_one(consumer, "folder.resource.deleted")
        await main._execute_or_defer_forced_deletion(session, folder)
        await asyncio.wait_for(got_message.wait(), timeout=5)
        assert received[0].payload == {"resource_id": folder.id}
    finally:
        for attr_name, had, original in swaps:
            if had:
                setattr(main.app.state, attr_name, original)
            else:
                delattr(main.app.state, attr_name)
        await event_bus.close()
