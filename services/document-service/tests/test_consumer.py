from dms_db_base import make_session_factory
from dms_eventbus_client import Event
from document_service import consumer, repository


def _session_factory(engine):
    return make_session_factory(engine)


async def _upload_and_lock(session, *, document_id="doc-1", locked_by="alice"):
    await repository.create_document(
        session,
        document_id=document_id,
        title="Vertrag",
        filename="vertrag.pdf",
        content_type="application/pdf",
        size_bytes=3,
        checksum_sha256="abc",
        storage_object_key="documents/doc-1/abc",
        folder_id=None,
        object_type_id=None,
        attributes={},
        created_by=locked_by,
    )
    await repository.acquire_lock(
        session, document_id, locked_by=locked_by, session_id="s1", timeout_seconds=1800.0
    )
    await session.commit()


async def test_approved_force_unlock_releases_lock_and_publishes(engine):
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await _upload_and_lock(session)

    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(session_factory, fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-1",
            "action_type": "document.force_unlock",
            "initiated_by": "admin",
            "approved_by": "supervisor",
            "payload": {"document_id": "doc-1", "released_by": "admin", "reason": "Urlaub"},
        },
    )

    await handler(event.to_bytes())

    async with session_factory() as session:
        lock = await repository.get_lock(session, "doc-1")
    assert lock is None
    assert published == [
        (
            "document.lock.force_released",
            "doc-1",
            {"original_locked_by": "alice", "released_by": "admin", "reason": "Urlaub"},
        )
    ]


async def test_unrelated_action_type_is_ignored(engine):
    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-2",
            "action_type": "permission.scope_lock.create",
            "initiated_by": "admin",
            "approved_by": "supervisor",
            "payload": {"resource_id": "root", "locked_by": "admin"},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []


async def test_approved_force_unlock_for_already_unlocked_document_is_logged_not_raised(engine):
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await repository.create_document(
            session,
            document_id="doc-2",
            title="Vertrag",
            filename="vertrag.pdf",
            content_type="application/pdf",
            size_bytes=3,
            checksum_sha256="abc",
            storage_object_key="documents/doc-2/abc",
            folder_id=None,
            object_type_id=None,
            attributes={},
            created_by="alice",
        )
        await session.commit()

    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(session_factory, fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-3",
            "action_type": "document.force_unlock",
            "initiated_by": "admin",
            "approved_by": "supervisor",
            "payload": {"document_id": "doc-2", "released_by": "admin", "reason": None},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen, obwohl keine Sperre existiert

    assert published == []


async def test_approved_event_without_document_id_is_logged_not_raised(engine):
    """Regression: ein Fremd-/Fehlform-Request mit demselben action_type,
    aber ohne document_id im payload (z. B. versehentlich über
    /approval-requests angelegt), darf den Konsumenten nicht crashen -
    sonst bleibt die NATS-Nachricht unbestätigt und wird endlos erneut
    zugestellt."""
    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-4",
            "action_type": "document.force_unlock",
            "initiated_by": "admin",
            "approved_by": "supervisor",
            "payload": {"x": 1},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []
