from audit_service import repository
from audit_service.models import AuditEvent
from dms_eventbus_client import Event


def make_event(**overrides) -> Event:
    defaults = dict(
        event_type="registry.instance.registered",
        service_name="registry-service",
        subject="doc-1",
        payload={"service_type": "document-service"},
    )
    defaults.update(overrides)
    return Event(**defaults)


async def test_append_first_event_chains_to_genesis(session):
    entry = await repository.append_event(session, make_event())

    assert entry.prev_hash == "0" * 64
    assert len(entry.hash) == 64


async def test_append_second_event_chains_to_first(session):
    first = await repository.append_event(session, make_event())
    second = await repository.append_event(session, make_event(subject="doc-2"))

    assert second.prev_hash == first.hash
    assert second.hash != first.hash


async def test_append_is_idempotent_by_event_id(session):
    event = make_event()

    first = await repository.append_event(session, event)
    second = await repository.append_event(session, event)

    assert first.id == second.id
    events = await repository.list_events(session)
    assert len(events) == 1


async def test_verify_chain_ok_for_untouched_events(session):
    await repository.append_event(session, make_event())
    await repository.append_event(session, make_event(subject="doc-2"))
    await repository.append_event(session, make_event(subject="doc-3"))

    result = await repository.verify_chain(session)

    assert result.ok is True
    assert result.checked == 3
    assert result.broken_at_id is None


async def test_verify_chain_detects_tampering(session):
    await repository.append_event(session, make_event())
    tampered = await repository.append_event(session, make_event(subject="doc-2"))
    await repository.append_event(session, make_event(subject="doc-3"))

    # Direkter DB-Zugriff, am Repository vorbei - simuliert nachträgliche Manipulation.
    tampered.payload = {"service_type": "tampered"}
    await session.flush()

    result = await repository.verify_chain(session)

    assert result.ok is False
    assert result.broken_at_id == tampered.id


async def test_verify_chain_empty_is_ok(session):
    result = await repository.verify_chain(session)

    assert result.ok is True
    assert result.checked == 0


async def test_list_events_respects_limit(session):
    for i in range(5):
        await repository.append_event(session, make_event(subject=f"doc-{i}"))

    events: list[AuditEvent] = await repository.list_events(session, limit=2)

    assert len(events) == 2
