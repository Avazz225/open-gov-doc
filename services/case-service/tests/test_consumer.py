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


class FakeObjectTypeClient:
    """Ersetzt den echten HTTP-Client gegen object-type-service (Phase 45
    Session 4) - gleiches Fake-Muster wie `FakeDocumentClient` oben statt
    eines echten Aufrufs, da dieser Handler direkt aufgerufen wird. Nie
    aufgerufen fuer Faelle mit `object_type_id=None` (siehe
    `status_transitions.close_with_validation`), daher als Platzhalter mit
    leerer Fehlerliste in den Tests unveraendert von diesem Session
    ausreichend."""

    def __init__(self, errors: list[str] | None = None):
        self._errors = errors or []
        self.calls: list[tuple] = []

    async def validate(
        self, object_type_id: int, *, name: str, attributes: dict, status_transition=None
    ) -> list[str]:
        self.calls.append((object_type_id, name, attributes, status_transition))
        return self._errors


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
    handler = consumer.make_handler(
        session_factory, document_client, fake_publish, FakeObjectTypeClient()
    )
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

    handler = consumer.make_handler(
        session_factory, FakeDocumentClient({}), fake_publish, FakeObjectTypeClient()
    )
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

    handler = consumer.make_handler(
        session_factory, FakeDocumentClient({}), fake_publish, FakeObjectTypeClient()
    )
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

    handler = consumer.make_handler(
        session_factory, FakeDocumentClient({}), fake_publish, FakeObjectTypeClient()
    )
    event = Event(
        event_type="workflow.instance.completed",
        service_name="workflow-service",
        subject="instance-1",
        payload={"business_key": None},
    )

    await handler(event.to_bytes())

    assert published == []


async def test_completed_event_blocked_by_object_type_leaves_case_open(engine):
    """Phase 45 Session 4 (ADR 0165's successor) - the constraint engine
    rejecting the open->closed transition leaves the case `"open"` and
    publishes `case.close_blocked` instead of `case.closed`."""
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await repository.create_case(
            session,
            case_id="case-1",
            name="Umlaufmappe",
            object_type_id=42,
            attributes={},
            process_definition_id=1,
            process_instance_id="instance-1",
            created_by="alice",
        )
        await session.commit()

    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    object_type_client = FakeObjectTypeClient(errors=["Statusübergang erfordert Grund"])
    handler = consumer.make_handler(
        session_factory, FakeDocumentClient({}), fake_publish, object_type_client
    )
    event = Event(
        event_type="workflow.instance.completed",
        service_name="workflow-service",
        subject="instance-1",
        payload={"business_key": "case-1"},
    )

    await handler(event.to_bytes())

    async with session_factory() as session:
        case = await repository.get_case(session, "case-1")
        assert case.status == "open"
        assert case.closed_at is None
    assert published == [
        ("case.close_blocked", "case-1", {"errors": ["Statusübergang erfordert Grund"]})
    ]
    assert object_type_client.calls == [(42, "Umlaufmappe", {}, {"from": "open", "to": "closed"})]


async def test_completed_event_allowed_by_object_type_closes_normally(engine):
    """Mirror of the blocking test above - an object type WITHOUT a matching
    `statusTransitions` entry (or one that is satisfied) still closes the
    case as before Phase 45 Session 4."""
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await repository.create_case(
            session,
            case_id="case-1",
            name="Umlaufmappe",
            object_type_id=42,
            attributes={},
            process_definition_id=1,
            process_instance_id="instance-1",
            created_by="alice",
        )
        await session.commit()

    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    object_type_client = FakeObjectTypeClient(errors=[])
    handler = consumer.make_handler(
        session_factory, FakeDocumentClient({}), fake_publish, object_type_client
    )
    event = Event(
        event_type="workflow.instance.completed",
        service_name="workflow-service",
        subject="instance-1",
        payload={"business_key": "case-1"},
    )

    await handler(event.to_bytes())

    async with session_factory() as session:
        case = await repository.get_case(session, "case-1")
        assert case.status == "closed"
        assert case.closed_at is not None
    assert published == [("case.closed", "case-1", {"process_instance_id": "instance-1"})]
