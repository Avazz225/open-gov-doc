import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from permission_service import repository

logger = logging.getLogger(__name__)


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: Callable[[str, dict], Awaitable[None]],
) -> Callable[[bytes], Awaitable[None]]:
    """Executes this service's own gated actions (scope locks, 4.7, role
    assignments since P17-S3, 4.3/14.2, and role creation since P32-S1,
    ADR 0130) only after approval (4.3) - self-consumption of its own
    `permission.approval.approved` event, the exact same mechanism used for
    other services (e.g. `document-service`). Action types that do not
    belong to this service (e.g. `document.force_unlock`) are ignored."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        action_type = event.payload.get("action_type")
        known_action_types = (
            "permission.scope_lock.create",
            "permission.scope_lock.release",
            "system.not_shutdown.trigger",
            "permission.role_assignment.create",
            "permission.role.create",
            "permission.role.update",
        )
        if action_type not in known_action_types:
            return
        action_payload = event.payload.get("payload") or {}

        async with session_factory() as session:
            try:
                if action_type == "system.not_shutdown.trigger":
                    mode = await repository.activate_maintenance_mode(
                        session,
                        triggered_by=action_payload["triggered_by"],
                        reason=action_payload.get("reason"),
                    )
                    await session.commit()
                    await publish_event(
                        "permission.maintenance_mode.activated",
                        {"triggered_by": mode.triggered_by, "reason": mode.reason},
                        actor=mode.triggered_by,
                    )
                elif action_type == "permission.scope_lock.create":
                    expires_at_raw = action_payload.get("expires_at")
                    lock = await repository.create_scope_lock(
                        session,
                        resource_id=action_payload["resource_id"],
                        locked_by=action_payload["locked_by"],
                        reason=action_payload.get("reason"),
                        blocks_read=action_payload.get("blocks_read", False),
                        expires_at=(
                            datetime.fromisoformat(expires_at_raw) if expires_at_raw else None
                        ),
                    )
                    await session.commit()
                    await publish_event(
                        "permission.scope_lock.created",
                        {
                            "scope_lock_id": lock.id,
                            "resource_id": lock.resource_id,
                            "locked_by": lock.locked_by,
                            "reason": lock.reason,
                            "blocks_read": lock.blocks_read,
                        },
                        actor=lock.locked_by,
                    )
                elif action_type == "permission.scope_lock.release":
                    lock = await repository.release_scope_lock(
                        session, action_payload["lock_id"], action_payload["released_by"]
                    )
                    await session.commit()
                    await publish_event(
                        "permission.scope_lock.released",
                        {
                            "scope_lock_id": lock.id,
                            "resource_id": lock.resource_id,
                            "released_by": lock.released_by,
                        },
                        actor=lock.released_by,
                    )
                elif action_type == "permission.role_assignment.create":
                    # 14.2 "permission change" - `create_role_assignment` is
                    # already idempotency-friendly, guarded via
                    # `repository.NotFoundError` (unknown role_id/resource),
                    # see the except branch below.
                    assignment = await repository.create_role_assignment(
                        session,
                        principal_type=action_payload["principal_type"],
                        principal_id=action_payload["principal_id"],
                        role_id=action_payload["role_id"],
                        resource_id=action_payload["resource_id"],
                    )
                    await session.commit()
                    await publish_event(
                        "permission.role_assignment.created",
                        {
                            "role_assignment_id": assignment.id,
                            "principal_type": assignment.principal_type,
                            "principal_id": assignment.principal_id,
                            "role_id": assignment.role_id,
                            "resource_id": assignment.resource_id,
                        },
                        actor=assignment.principal_id,
                    )
                elif action_type == "permission.role.update":
                    # Post-Roadmap Phase 39 Session 2 (ADR 0151) - unlike
                    # `create_role`, `update_role` already has a real
                    # not-found precondition (`repository.NotFoundError`,
                    # caught below like every other branch here) - a role
                    # deleted between request and approval surfaces as a
                    # logged, swallowed warning instead of an endless NATS
                    # redelivery loop.
                    role = await repository.update_role(
                        session,
                        action_payload["role_id"],
                        description=action_payload.get("description", ""),
                        permissions=action_payload.get("permissions", []),
                    )
                    await session.commit()
                    await publish_event(
                        "permission.role.updated",
                        {
                            "role_id": role.id,
                            "name": role.name,
                            "description": role.description,
                            "permissions": role.permissions,
                        },
                        actor=event.payload.get("approved_by"),
                    )
                else:
                    # permission.role.create (P32-S1, ADR 0130) -
                    # `create_role` has no not-found precondition (it only
                    # ever inserts), so this branch has no
                    # `repository.NotFoundError` case of its own - a
                    # duplicate `name` would surface as an `IntegrityError`
                    # instead, deliberately left unguarded like every other
                    # branch here (a second, distinct decision to fix would
                    # be adding idempotency, not this session's scope).
                    role = await repository.create_role(
                        session,
                        action_payload["name"],
                        action_payload.get("description", ""),
                        action_payload.get("permissions", []),
                    )
                    await session.commit()
                    await publish_event(
                        "permission.role.created",
                        {
                            "role_id": role.id,
                            "name": role.name,
                            "description": role.description,
                            "permissions": role.permissions,
                        },
                        actor=event.payload.get("approved_by"),
                    )
            except (repository.NotFoundError, KeyError):
                # KeyError covers foreign/malformed payloads (e.g. a request
                # created for test purposes with the same action_type but
                # without the fields expected here) - log instead of
                # crashing, otherwise the NATS message stays unacknowledged
                # and gets redelivered endlessly (see dms-eventbus-client).
                logger.warning(
                    "Genehmigte Aktion %r konnte nicht ausgeführt werden (Ressource/Sperre "
                    "inzwischen nicht mehr vorhanden oder Payload unvollständig) - "
                    "request_id=%s, payload=%r",
                    action_type,
                    event.payload.get("request_id"),
                    action_payload,
                )

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: Callable[[str, dict], Awaitable[None]],
) -> None:
    handler = make_handler(session_factory, publish_event)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="permission-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
