"""Structure events (`*.resource.*`) - P98-S1, ADR 0268."""

from datetime import UTC, datetime

from dms_db_base import make_session_factory
from dms_eventbus_client import Event
from permission_service import repository
from permission_service.models import ResourceNode, RoleAssignment, ScopeLock
from permission_service.structure_consumer import make_handler
from sqlalchemy import func, select


def _event(event_type: str, payload: dict) -> bytes:
    return Event(
        event_type=event_type,
        service_name="folder-service",
        subject=payload["resource_id"],
        payload=payload,
    ).to_bytes()


async def test_deleting_a_resource_removes_assignments_and_locks_of_its_subtree(engine):
    """Until P98 a deleted folder that still had a role assignment failed on
    role_assignment_resource_id_fkey; JetStream redelivered it forever and
    blocked every later structure event, so new folders stayed unregistered."""
    factory = make_session_factory(engine)
    handle = make_handler(factory)
    await handle(_event("folder.resource.created", {"resource_id": "sc-akte", "parent_id": None}))
    await handle(
        _event("folder.resource.created", {"resource_id": "sc-unter", "parent_id": "sc-akte"})
    )
    async with factory() as session:
        role = await repository.create_role(session, "sc-reader", "", ["folder.read"])
        session.add_all(
            [
                RoleAssignment(
                    principal_type="user",
                    principal_id="kaya",
                    role_id=role.id,
                    resource_id="sc-akte",
                ),
                RoleAssignment(
                    principal_type="user",
                    principal_id="erika",
                    role_id=role.id,
                    resource_id="sc-unter",
                ),
            ]
        )
        session.add(
            ScopeLock(
                resource_id="sc-unter",
                locked_by="alice",
                reason="Prüfung",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    await handle(_event("folder.resource.deleted", {"resource_id": "sc-akte"}))

    async with factory() as session:
        assert await session.get(ResourceNode, "sc-akte") is None
        assert await session.get(ResourceNode, "sc-unter") is None
        remaining = await session.scalar(
            select(func.count())
            .select_from(RoleAssignment)
            .where(RoleAssignment.resource_id.in_(["sc-akte", "sc-unter"]))
        )
        assert remaining == 0
        locks = await session.scalar(
            select(func.count()).select_from(ScopeLock).where(ScopeLock.resource_id == "sc-unter")
        )
        assert locks == 0


async def test_deleting_an_unknown_resource_is_a_no_op(engine):
    handle = make_handler(make_session_factory(engine))
    # Must not raise (a redelivered or late event for a node that is gone).
    await handle(_event("folder.resource.deleted", {"resource_id": "sc-gibt-es-nicht"}))
