"""Watch subscriptions (P90-S4, ADR 0256): the API, the pure event matching,
and the per-watcher delivery rules."""

from unittest.mock import AsyncMock

import pytest
from dms_eventbus_client import Event
from fastapi.testclient import TestClient
from favorite_service import main, watch_consumer, watches
from favorite_service.main import app

ALICE = {"X-DMS-Username": "alice", "X-DMS-Principal": "sub-alice"}


@pytest.fixture
def session_factory(engine):
    from dms_db_base import make_session_factory

    return make_session_factory(engine)


@pytest.fixture
def published(monkeypatch):
    events = []

    async def record(event_type, subject, payload, actor=None):
        events.append((event_type, subject, payload, actor))

    monkeypatch.setattr(main, "publish_event", record)
    return events


@pytest.fixture
def client(published):
    with TestClient(app) as c:
        app.state.permission_client = AsyncMock()
        app.state.permission_client.check_batch.side_effect = lambda **kw: {
            rid: not rid.startswith("secret") for rid in kw["resource_ids"]
        }
        yield c


def _watch(client, object_type="document", object_id="doc-1", headers=ALICE):
    return client.post(
        "/watches",
        json={"user_id": "alice", "object_type": object_type, "object_id": object_id},
        headers=headers,
    )


def test_watch_needs_the_owner_and_read_access(client):
    assert (
        _watch(client, headers={"X-DMS-Username": "bob", "X-DMS-Principal": "sub-bob"}).status_code
        == 403
    )
    assert _watch(client, headers={"X-DMS-Username": "alice"}).status_code == 401
    assert _watch(client, object_id="secret-1").status_code == 403
    created = _watch(client)
    assert created.status_code == 201
    assert _watch(client).status_code == 409
    # The read check ran as the caller's principal.
    call = app.state.permission_client.check_batch.call_args.kwargs
    assert (call["principal_id"], call["permission"]) == ("sub-alice", "document.read")


def test_list_filter_and_unwatch(client, published):
    _watch(client)
    _watch(client, object_type="folder", object_id="f-1")
    listed = client.get("/watches", params={"user_id": "alice"}, headers=ALICE).json()
    assert {(w["object_type"], w["object_id"]) for w in listed} == {
        ("document", "doc-1"),
        ("folder", "f-1"),
    }
    one = client.get(
        "/watches",
        params={"user_id": "alice", "object_type": "folder", "object_id": "f-1"},
        headers=ALICE,
    ).json()
    assert len(one) == 1
    assert client.get("/watches", params={"user_id": "bob"}, headers=ALICE).status_code == 403
    params = {"user_id": "alice", "object_type": "folder", "object_id": "f-1"}
    assert client.delete("/watches", params=params, headers=ALICE).status_code == 204
    assert client.delete("/watches", params=params, headers=ALICE).status_code == 404
    assert [e[0] for e in published].count("favorite.watch.added") == 2


def test_changes_for_maps_document_folder_and_child_events():
    def kinds(event_type, subject, payload=None):
        return [
            (c.object_type, c.object_id, c.change)
            for c in watches.changes_for(event_type, subject, payload or {})
        ]

    assert kinds("document.version.created", "d1", {"version_number": 2}) == [
        ("document", "d1", "new_version")
    ]
    assert kinds("document.created", "d1", {"folder_id": "f1", "title": "Antrag"}) == [
        ("folder", "f1", "document_added")
    ]
    # A move concerns the document and, as "moved in", its new folder.
    assert kinds("document.resource.moved", "d1", {"new_parent_id": "f2"}) == [
        ("document", "d1", "moved"),
        ("folder", "f2", "document_moved_in"),
    ]
    assert kinds("folder.updated", "f1", {"name": "Neu"}) == [("folder", "f1", "folder_updated")]
    assert kinds("document.viewed", "d1") == []
    assert kinds("document.version.created", None) == []


async def _seed(session_factory, *rows):
    from datetime import UTC, datetime

    async with session_factory() as session:
        for user, principal, object_type, object_id in rows:
            await watches.create_watch(
                session,
                user_id=user,
                principal_id=principal,
                object_type=object_type,
                object_id=object_id,
                now=datetime.now(UTC),
            )
        await session.commit()


async def test_handle_event_notifies_watchers_except_the_actor_and_the_unauthorized(
    session_factory,
):
    await _seed(
        session_factory,
        ("alice", "sub-alice", "folder", "f1"),
        ("bob", "sub-bob", "folder", "f1"),
        ("carol", "sub-carol", "folder", "f1"),
    )
    sent = []

    async def publish(event_type, subject, payload, actor=None):
        sent.append(payload)

    async def can_read(principal_id, object_type, ids):
        return {i: principal_id != "sub-carol" for i in ids}  # carol lost access

    event = Event(
        event_type="document.created",
        service_name="document-service",
        subject="d9",
        payload={"title": "Bescheid.pdf", "folder_id": "f1"},
        actor="sub-alice",  # alice uploaded it herself
    )
    count = await watch_consumer.handle_event(
        event, session_factory=session_factory, can_read=can_read, publish_event=publish
    )
    assert count == 1
    (payload,) = sent
    assert (payload["user_id"], payload["change"], payload["subject_id"], payload["title"]) == (
        "bob",
        "document_added",
        "d9",
        "Bescheid.pdf",
    )


async def test_deletions_skip_the_read_check_and_a_failing_check_skips_only_that_watcher(
    session_factory,
):
    await _seed(session_factory, ("bob", "sub-bob", "document", "d1"))
    sent = []

    async def publish(event_type, subject, payload, actor=None):
        sent.append(payload["change"])

    async def broken(principal_id, object_type, ids):
        raise RuntimeError("permission-service down")

    gone = Event(event_type="document.force_deleted", service_name="d", subject="d1", payload={})
    changed = Event(
        event_type="document.metadata.updated", service_name="d", subject="d1", payload={}
    )
    for event in (gone, changed):
        await watch_consumer.handle_event(
            event, session_factory=session_factory, can_read=broken, publish_event=publish
        )
    assert sent == ["deleted"]
