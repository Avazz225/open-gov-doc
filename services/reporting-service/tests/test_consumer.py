from dms_db_base import make_session_factory
from dms_eventbus_client import Event
from reporting_service import repository
from reporting_service.consumer import make_handler
from sqlalchemy import select


async def test_document_created_event_is_recorded(engine):
    session_factory = make_session_factory(engine)
    handler = make_handler(session_factory)

    event = Event(
        event_type="document.created",
        service_name="document-service",
        subject="doc-1",
        payload={"title": "Vertrag", "created_by": "alice", "folder_id": "folder-1"},
    )
    await handler(event.to_bytes())

    async with session_factory() as session:
        rows = await repository.get_document_volume(session, group_by="day")

    assert len(rows) == 1
    assert rows[0][1] == "folder-1"


async def test_document_created_without_folder_id_records_null_folder(engine):
    session_factory = make_session_factory(engine)
    handler = make_handler(session_factory)

    event = Event(
        event_type="document.created",
        service_name="document-service",
        subject="doc-2",
        payload={"title": "Vertrag", "created_by": "alice"},
    )
    await handler(event.to_bytes())

    async with session_factory() as session:
        rows = await repository.get_document_volume(session, group_by="day")

    assert rows[0][1] is None


async def test_other_document_events_are_ignored(engine):
    session_factory = make_session_factory(engine)
    handler = make_handler(session_factory)

    event = Event(
        event_type="document.deleted",
        service_name="document-service",
        subject="doc-3",
        payload={"deleted_by": "alice"},
    )
    await handler(event.to_bytes())

    async with session_factory() as session:
        from reporting_service.models import DocumentCreatedEvent

        result = await session.execute(select(DocumentCreatedEvent))
        assert result.scalars().all() == []
