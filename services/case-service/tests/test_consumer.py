from case_service import consumer, repository
from dms_db_base import make_session_factory
from dms_eventbus_client import Event


class FakeDocumentClient:
    """Ersetzt den echten HTTP-Client (kein NATS/HTTP in diesem Test noetig,
    gleiches Muster wie notification-service/tests/test_consumer.py - Handler
    wird direkt aufgerufen statt ueber echtes NATS)."""

    def __init__(self, documents: dict[str, dict | None]):
        self._documents = documents

    async def get(self, document_id: str) -> dict | None:
        return self._documents.get(document_id)


def _session_factory(engine):
    return make_session_factory(engine)


async def test_completed_event_closes_matching_case_and_fixes_snapshot(engine):
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        case = await repository.create_case(
            session,
            case_id="case-1",
            name="Umlaufmappe",
            object_type_id=None,
            attributes={},
            process_definition_id=1,
            process_instance_id="instance-1",
            created_by="alice",
        )
        await repository.add_document_reference(
            session, case.id, document_id="doc-1", added_by="alice"
        )
        await session.commit()

    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    document_client = FakeDocumentClient({"doc-1": {"current_version_number": 5}})
    handler = consumer.make_handler(session_factory, document_client, fake_publish)
    event = Event(
        event_type="workflow.instance.completed",
        service_name="workflow-service",
        subject="instance-1",
        payload={"business_key": "case-1"},
    )

    await handler(event.to_bytes())

    async with session_factory() as session:
        closed = await repository.get_case(session, "case-1")
        assert closed.status == "closed"
        assert closed.closed_at is not None
        references = await repository.list_document_references(session, "case-1")
        assert references[0].snapshot_version_number == 5
    assert published == [("case.closed", "case-1", {"process_instance_id": "instance-1"})]


async def test_completed_event_without_matching_case_is_ignored(engine):
    session_factory = _session_factory(engine)
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(session_factory, FakeDocumentClient({}), fake_publish)
    event = Event(
        event_type="workflow.instance.completed",
        service_name="workflow-service",
        subject="instance-99",
        payload={"business_key": "unknown-case"},
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []


async def test_completed_event_for_already_closed_case_is_ignored(engine):
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        case = await repository.create_case(
            session,
            case_id="case-1",
            name="Umlaufmappe",
            object_type_id=None,
            attributes={},
            process_definition_id=1,
            process_instance_id="instance-1",
            created_by="alice",
        )
        await repository.close_case(session, case, snapshots={})
        await session.commit()

    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(session_factory, FakeDocumentClient({}), fake_publish)
    event = Event(
        event_type="workflow.instance.completed",
        service_name="workflow-service",
        subject="instance-1",
        payload={"business_key": "case-1"},
    )

    await handler(event.to_bytes())

    assert published == []


async def test_completed_event_without_business_key_is_ignored(engine):
    session_factory = _session_factory(engine)
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(session_factory, FakeDocumentClient({}), fake_publish)
    event = Event(
        event_type="workflow.instance.completed",
        service_name="workflow-service",
        subject="instance-1",
        payload={"business_key": None},
    )

    await handler(event.to_bytes())

    assert published == []
