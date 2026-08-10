from datetime import UTC, datetime

from audit_service import repository
from audit_service.hashchain import GENESIS_HASH, compute_hash
from audit_service.models import AuditEvent, AuditMeta
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


async def _insert_pre_migration_row(session, *, prev_hash: str, subject: str) -> AuditEvent:
    """Legt eine Zeile genau so an, wie sie VOR P7-S2 gehasht worden wäre
    (kein ``actor`` im kanonischen JSON) - simuliert echte Alt-Historie für
    die Cutover-Tests, ohne auf ``repository.append_event`` (das seit P7-S2
    immer ``include_actor=True`` verwendet) zurückgreifen zu können."""
    occurred_at = datetime.now(UTC)
    recorded_at = datetime.now(UTC)
    fields = repository._hashable_fields(
        "evt-" + subject,
        "registry.instance.registered",
        occurred_at,
        "registry-service",
        subject,
        {"service_type": "document-service"},
        recorded_at,
        include_actor=False,
    )
    entry = AuditEvent(
        event_id="evt-" + subject,
        event_type="registry.instance.registered",
        occurred_at=occurred_at,
        service_name="registry-service",
        subject=subject,
        payload={"service_type": "document-service"},
        actor=None,
        recorded_at=recorded_at,
        prev_hash=prev_hash,
        hash=compute_hash(prev_hash, fields),
    )
    session.add(entry)
    await session.flush()
    return entry


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


async def test_append_event_stores_actor(session):
    entry = await repository.append_event(session, make_event(actor="alice"))

    assert entry.actor == "alice"


async def test_get_actor_field_cutover_id_is_zero_for_fresh_chain(session):
    cutover = await repository.get_actor_field_cutover_id(session)

    assert cutover == 0


async def test_get_actor_field_cutover_id_is_idempotent(session):
    # append_event ruft get_actor_field_cutover_id bereits VOR dem Insert auf
    # (siehe repository.append_event) - bei einer leeren Kette ist der
    # Cutover deshalb 0, nicht die id des ersten Events.
    await repository.append_event(session, make_event())

    first = await repository.get_actor_field_cutover_id(session)
    await repository.append_event(session, make_event(subject="doc-2"))
    second = await repository.get_actor_field_cutover_id(session)

    assert first == second == 0


async def test_verify_chain_ok_across_actor_field_cutover_boundary(session):
    """Simuliert den echten P7-S2-Migrationsfall: bestehende Alt-Zeilen ohne
    ``actor`` im Hash-Input, gefolgt von neuen Zeilen mit ``actor`` - die
    Kette muss über die Grenze hinweg weiterhin vollständig verifizierbar
    bleiben (siehe Architekturentscheidung "Cutover-Versionierung")."""
    old_first = await _insert_pre_migration_row(session, prev_hash=GENESIS_HASH, subject="doc-1")
    old_second = await _insert_pre_migration_row(session, prev_hash=old_first.hash, subject="doc-2")
    # Cutover manuell auf den Stand VOR der Migration setzen - entspricht
    # dem, was main.py's Lifespan beim ersten Start nach dem Deploy tut.
    # Beide Cutover-Felder (actor seit P7-S2, on_behalf_of seit P14-S11)
    # müssen hier gleichermaßen VOR die beiden Alt-Zeilen gesetzt werden -
    # sonst würde `on_behalf_of_field_cutover_id`s Python-seitiger Default
    # (0) beide Alt-Zeilen fälschlich als "nach dem on_behalf_of-Cutover"
    # einstufen, obwohl ihr gespeicherter Hash das Feld nie enthielt.
    session.add(
        AuditMeta(
            id=1,
            actor_field_cutover_id=old_second.id,
            on_behalf_of_field_cutover_id=old_second.id,
        )
    )
    await session.flush()

    new_entry = await repository.append_event(session, make_event(subject="doc-3", actor="alice"))
    assert new_entry.prev_hash == old_second.hash

    result = await repository.verify_chain(session)

    assert result.ok is True
    assert result.checked == 3
    assert result.broken_at_id is None


async def test_list_events_filters_by_actor(session):
    await repository.append_event(session, make_event(subject="doc-1", actor="alice"))
    await repository.append_event(session, make_event(subject="doc-2", actor="bob"))

    events = await repository.list_events(session, actor="alice")

    assert len(events) == 1
    assert events[0].subject == "doc-1"


async def test_list_events_filters_by_subject(session):
    await repository.append_event(session, make_event(subject="doc-1"))
    await repository.append_event(session, make_event(subject="doc-2"))

    events = await repository.list_events(session, subject="doc-2")

    assert len(events) == 1
    assert events[0].subject == "doc-2"


async def test_list_events_filters_by_exact_event_type(session):
    await repository.append_event(session, make_event(event_type="document.created"))
    await repository.append_event(session, make_event(event_type="document.deleted"))

    events = await repository.list_events(session, event_type="document.created")

    assert len(events) == 1
    assert events[0].event_type == "document.created"


async def test_list_events_filters_by_event_type_wildcard(session):
    await repository.append_event(session, make_event(event_type="document.created"))
    await repository.append_event(session, make_event(event_type="document.deleted"))
    await repository.append_event(session, make_event(event_type="folder.created"))

    events = await repository.list_events(session, event_type="document.>")

    assert {e.event_type for e in events} == {"document.created", "document.deleted"}


async def test_list_events_filters_by_time_window(session):
    await repository.append_event(
        session, make_event(subject="doc-1", occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
    )
    await repository.append_event(
        session, make_event(subject="doc-2", occurred_at=datetime(2026, 6, 1, tzinfo=UTC))
    )
    await repository.append_event(
        session, make_event(subject="doc-3", occurred_at=datetime(2026, 12, 1, tzinfo=UTC))
    )

    events = await repository.list_events(
        session,
        since=datetime(2026, 3, 1, tzinfo=UTC),
        until=datetime(2026, 9, 1, tzinfo=UTC),
    )

    assert len(events) == 1
    assert events[0].subject == "doc-2"


# --- Stellvertretung bei Abwesenheit (4.4a, P14-S11) -------------------------


async def _insert_pre_on_behalf_of_migration_row(
    session, *, prev_hash: str, subject: str, actor: str | None
) -> AuditEvent:
    """Wie ``_insert_pre_migration_row``, aber simuliert speziell eine Zeile
    von NACH dem actor-Cutover (P7-S2), aber VOR dem on_behalf_of-Cutover
    (P14-S11) - genau der reale Migrationsfall dieser Session: ``actor``
    IST bereits im Hash-Input, ``on_behalf_of`` NOCH NICHT."""
    occurred_at = datetime.now(UTC)
    recorded_at = datetime.now(UTC)
    fields = repository._hashable_fields(
        "evt-" + subject,
        "registry.instance.registered",
        occurred_at,
        "registry-service",
        subject,
        {"service_type": "document-service"},
        recorded_at,
        actor=actor,
        include_actor=True,
        include_on_behalf_of=False,
    )
    entry = AuditEvent(
        event_id="evt-" + subject,
        event_type="registry.instance.registered",
        occurred_at=occurred_at,
        service_name="registry-service",
        subject=subject,
        payload={"service_type": "document-service"},
        actor=actor,
        on_behalf_of=None,
        recorded_at=recorded_at,
        prev_hash=prev_hash,
        hash=compute_hash(prev_hash, fields),
    )
    session.add(entry)
    await session.flush()
    return entry


async def test_append_event_stores_on_behalf_of(session):
    entry = await repository.append_event(session, make_event(actor="bob", on_behalf_of="alice"))

    assert entry.actor == "bob"
    assert entry.on_behalf_of == "alice"


async def test_get_on_behalf_of_field_cutover_id_is_zero_for_fresh_chain(session):
    cutover = await repository.get_on_behalf_of_field_cutover_id(session)

    assert cutover == 0


async def test_get_on_behalf_of_field_cutover_id_is_idempotent(session):
    await repository.append_event(session, make_event())

    first = await repository.get_on_behalf_of_field_cutover_id(session)
    await repository.append_event(session, make_event(subject="doc-2"))
    second = await repository.get_on_behalf_of_field_cutover_id(session)

    assert first == second == 0


async def test_verify_chain_ok_across_on_behalf_of_field_cutover_boundary(session):
    """Realer Migrationsfall dieser Session: bestehende Zeilen, die den
    actor-Cutover (P7-S2) bereits hinter sich haben (``actor`` im Hash-
    Input), aber noch VOR dem neuen on_behalf_of-Cutover (P14-S11) liegen -
    gefolgt von neuen Zeilen mit beiden Feldern. Beide Cutover-Grenzen
    müssen unabhängig voneinander korrekt respektiert werden."""
    old_first = await _insert_pre_on_behalf_of_migration_row(
        session, prev_hash=GENESIS_HASH, subject="doc-1", actor="alice"
    )
    old_second = await _insert_pre_on_behalf_of_migration_row(
        session, prev_hash=old_first.hash, subject="doc-2", actor="bob"
    )
    # actor-Cutover liegt VOR beiden Alt-Zeilen (actor war schon aktiv),
    # on_behalf_of-Cutover liegt NACH beiden (das Feld gab es noch nicht) -
    # entspricht genau main.py's Lifespan-Zustand direkt nach diesem Deploy.
    session.add(
        AuditMeta(id=1, actor_field_cutover_id=0, on_behalf_of_field_cutover_id=old_second.id)
    )
    await session.flush()

    new_entry = await repository.append_event(
        session, make_event(subject="doc-3", actor="carol", on_behalf_of="dave")
    )
    assert new_entry.prev_hash == old_second.hash

    result = await repository.verify_chain(session)

    assert result.ok is True
    assert result.checked == 3
    assert result.broken_at_id is None


async def test_list_events_filters_by_on_behalf_of(session):
    await repository.append_event(
        session, make_event(subject="doc-1", actor="bob", on_behalf_of="alice")
    )
    await repository.append_event(session, make_event(subject="doc-2", actor="bob"))

    events = await repository.list_events(session, on_behalf_of="alice")

    assert len(events) == 1
    assert events[0].subject == "doc-1"
