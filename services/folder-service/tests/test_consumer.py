from unittest.mock import AsyncMock

from dms_db_base import make_session_factory
from dms_eventbus_client import Event
from folder_service import consumer, repository
from folder_service.models import Folder
from folder_service.settings import ROOT_FOLDER_ID


def _session_factory(engine):
    return make_session_factory(engine)


async def test_approved_force_delete_executes_physical_deletion(engine):
    """Zwangslöschung für Ordner (5.2a, seit P7-S1b) - identisches Muster wie
    document-service's `document.force_delete`-Konsument (P7-S1)."""
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await repository.ensure_root_folder(session)
        folder = await repository.create_folder(
            session,
            name="Geheime Akte",
            parent_id=ROOT_FOLDER_ID,
            object_type_id=None,
            attributes={},
            created_by="alice",
        )
        await session.commit()
        folder_id = folder.id

    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(session_factory, fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-1",
            "action_type": "folder.force_delete",
            "initiated_by": "system:retention-poll",
            "approved_by": "supervisor",
            "payload": {
                "folder_id": folder_id,
                "reason": "Frist abgelaufen",
                "triggered_by": "system:retention-poll",
            },
        },
    )

    await handler(event.to_bytes())

    async with session_factory() as session:
        try:
            await repository.get_folder(session, folder_id)
            raise AssertionError("Ordner hätte entfernt sein müssen")
        except repository.NotFoundError:
            pass
        entries = await repository.list_deletion_register(session, folder_id=folder_id)
        assert len(entries) == 1
        assert entries[0].trigger == "forced_deletion"

    expected_event = (
        "folder.force_deleted",
        folder_id,
        {"reason": "Frist abgelaufen", "triggered_by": "system:retention-poll"},
    )
    assert expected_event in published


async def test_approved_force_delete_for_already_removed_folder_is_logged_not_raised(engine):
    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-2",
            "action_type": "folder.force_delete",
            "initiated_by": "system:retention-poll",
            "approved_by": "supervisor",
            "payload": {"folder_id": "does-not-exist", "reason": None},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []


async def test_approved_event_without_folder_id_is_logged_not_raised(engine):
    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-3",
            "action_type": "folder.force_delete",
            "initiated_by": "admin",
            "approved_by": "supervisor",
            "payload": {"x": 1},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []


async def test_approved_delete_soft_deletes_folder(engine):
    """Löschantrag-Workflow für reguläre Nutzer (5.2, seit P7-S1c) -
    Genehmigung führt die zuvor per Vier-Augen-Prinzip zurückgestellte
    reguläre Papierkorb-Verschiebung tatsächlich aus (inkl. Kaskade)."""
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await repository.ensure_root_folder(session)
        folder = await repository.create_folder(
            session,
            name="Akte",
            parent_id=ROOT_FOLDER_ID,
            object_type_id=None,
            attributes={},
            created_by="alice",
        )
        await session.commit()
        folder_id = folder.id

    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    fake_document_client = AsyncMock()
    fake_document_client.cascade_trash.return_value = []
    handler = consumer.make_handler(session_factory, fake_publish, fake_document_client)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-5",
            "action_type": "folder.delete",
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": {"folder_id": folder_id, "deleted_by": "alice"},
        },
    )

    await handler(event.to_bytes())

    async with session_factory() as session:
        current = await session.get(Folder, folder_id)
        assert current.deleted_at is not None
    assert published == [("folder.trashed", folder_id, {"deleted_by": "alice"})]
    fake_document_client.cascade_trash.assert_awaited_once_with(
        [folder_id], via_folder_id=folder_id, deleted_by="alice"
    )


async def test_approved_delete_for_already_removed_folder_is_logged_not_raised(engine):
    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), fake_publish, AsyncMock())
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-6",
            "action_type": "folder.delete",
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": {"folder_id": "does-not-exist", "deleted_by": "alice"},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []


async def test_approved_delete_without_folder_id_is_logged_not_raised(engine):
    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), fake_publish, AsyncMock())
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-7",
            "action_type": "folder.delete",
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": {"x": 1},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []


async def test_unrelated_action_type_is_ignored(engine):
    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-4",
            "action_type": "document.force_delete",
            "initiated_by": "admin",
            "approved_by": "supervisor",
            "payload": {"document_id": "doc-1"},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []
