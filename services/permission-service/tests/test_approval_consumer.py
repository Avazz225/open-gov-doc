from dms_db_base import make_session_factory
from dms_eventbus_client import Event
from permission_service import approval_consumer, repository
from permission_service.settings import ROOT_RESOURCE_ID


def _session_factory(engine):
    return make_session_factory(engine)


async def test_approved_scope_lock_create_executes_and_publishes(engine):
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await repository.ensure_root_resource(session)
        await session.commit()

    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = approval_consumer.make_handler(session_factory, fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-1",
            "action_type": "permission.scope_lock.create",
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": {
                "resource_id": ROOT_RESOURCE_ID,
                "locked_by": "alice",
                "reason": "Migration",
                "blocks_read": False,
                "expires_at": None,
            },
        },
    )

    await handler(event.to_bytes())

    async with session_factory() as session:
        locks = await repository.list_scope_locks(session, ROOT_RESOURCE_ID)
    assert len(locks) == 1
    assert locks[0].locked_by == "alice"
    assert published == [
        (
            "permission.scope_lock.created",
            {
                "scope_lock_id": locks[0].id,
                "resource_id": ROOT_RESOURCE_ID,
                "locked_by": "alice",
                "reason": "Migration",
                "blocks_read": False,
            },
        )
    ]


async def test_approved_scope_lock_release_executes_and_publishes(engine):
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await repository.ensure_root_resource(session)
        await session.commit()
    async with session_factory() as session:
        lock = await repository.create_scope_lock(
            session,
            resource_id=ROOT_RESOURCE_ID,
            locked_by="alice",
            reason=None,
            blocks_read=False,
            expires_at=None,
        )
        await session.commit()
        lock_id = lock.id

    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = approval_consumer.make_handler(session_factory, fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-2",
            "action_type": "permission.scope_lock.release",
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": {"lock_id": lock_id, "released_by": "alice"},
        },
    )

    await handler(event.to_bytes())

    async with session_factory() as session:
        locks = await repository.list_scope_locks(session, ROOT_RESOURCE_ID)
    assert locks[0].released_by == "alice"
    assert published == [
        (
            "permission.scope_lock.released",
            {"scope_lock_id": lock_id, "resource_id": ROOT_RESOURCE_ID, "released_by": "alice"},
        )
    ]


async def test_unrelated_action_type_is_ignored(engine):
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = approval_consumer.make_handler(_session_factory(engine), fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-3",
            "action_type": "document.force_unlock",
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": {"document_id": "doc-1", "released_by": "alice"},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []


async def test_scope_lock_create_with_missing_keys_is_logged_not_raised(engine):
    """Regression: ein Fremd-/Fehlform-Request mit demselben action_type,
    aber fehlenden Pflichtfeldern (z. B. versehentlich über
    /approval-requests angelegt), darf den Konsumenten nicht crashen."""
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = approval_consumer.make_handler(_session_factory(engine), fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-5",
            "action_type": "permission.scope_lock.create",
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": {"x": 1},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []


async def test_missing_resource_at_execution_time_is_logged_not_raised(engine):
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = approval_consumer.make_handler(_session_factory(engine), fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-4",
            "action_type": "permission.scope_lock.create",
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": {
                "resource_id": "does-not-exist",
                "locked_by": "alice",
                "reason": None,
                "blocks_read": False,
                "expires_at": None,
            },
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []


async def test_approved_role_assignment_create_executes_and_publishes(engine):
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await repository.ensure_root_resource(session)
        role = await repository.create_role(session, "GatedRole", "", ["read"])
        await session.commit()
        role_id = role.id

    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = approval_consumer.make_handler(session_factory, fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-7",
            "action_type": "permission.role_assignment.create",
            "initiated_by": "gina",
            "approved_by": "bob",
            "payload": {
                "principal_type": "user",
                "principal_id": "gina",
                "role_id": role_id,
                "resource_id": ROOT_RESOURCE_ID,
            },
        },
    )

    await handler(event.to_bytes())

    async with session_factory() as session:
        assignments = await repository.list_role_assignments(session, principal_id="gina")
    assert len(assignments) == 1
    assert assignments[0].role_id == role_id
    assert published == [
        (
            "permission.role_assignment.created",
            {
                "role_assignment_id": assignments[0].id,
                "principal_type": "user",
                "principal_id": "gina",
                "role_id": role_id,
                "resource_id": ROOT_RESOURCE_ID,
            },
        )
    ]


async def test_role_assignment_create_with_missing_keys_is_logged_not_raised(engine):
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = approval_consumer.make_handler(_session_factory(engine), fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-8",
            "action_type": "permission.role_assignment.create",
            "initiated_by": "gina",
            "approved_by": "bob",
            "payload": {"x": 1},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []


async def test_approved_not_shutdown_trigger_activates_and_publishes(engine):
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await repository.ensure_root_resource(session)
        await session.commit()

    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = approval_consumer.make_handler(session_factory, fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-5",
            "action_type": "system.not_shutdown.trigger",
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": {"triggered_by": "alice", "reason": "Verdacht"},
        },
    )

    await handler(event.to_bytes())

    async with session_factory() as session:
        mode = await repository.get_or_seed_maintenance_mode(session)
    assert mode.active is True
    assert mode.triggered_by == "alice"
    assert published == [
        ("permission.maintenance_mode.activated", {"triggered_by": "alice", "reason": "Verdacht"})
    ]


async def test_not_shutdown_trigger_with_missing_keys_is_logged_not_raised(engine):
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = approval_consumer.make_handler(_session_factory(engine), fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-6",
            "action_type": "system.not_shutdown.trigger",
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": {"x": 1},
        },
    )

    await handler(event.to_bytes())  # darf nicht raisen

    assert published == []
