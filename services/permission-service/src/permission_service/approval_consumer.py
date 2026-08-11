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
    """Führt die eigenen gegateten Aktionen (Bereichssperren, 4.7, seit P17-S3
    zusätzlich Rollenzuweisungen, 4.3/14.2) erst nach Genehmigung aus (4.3) -
    Selbst-Konsum des eigenen `permission.approval.approved`-Events, exakt
    derselbe Mechanismus wie für fremde Services (z. B. `document-service`).
    Aktionstypen, die nicht zu diesem Service gehören (z. B.
    `document.force_unlock`), werden ignoriert."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        action_type = event.payload.get("action_type")
        known_action_types = (
            "permission.scope_lock.create",
            "permission.scope_lock.release",
            "system.not_shutdown.trigger",
            "permission.role_assignment.create",
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
                else:
                    # permission.role_assignment.create (P17-S3, 14.2
                    # "Berechtigungsänderung") - `create_role_assignment` ist
                    # bereits idempotent-freundlich über
                    # `repository.NotFoundError` abgesichert (unbekannte
                    # role_id/Ressource), siehe except-Zweig unten.
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
            except (repository.NotFoundError, KeyError):
                # KeyError deckt Fremd-/Fehlform-Payloads ab (z. B. ein zu
                # Testzwecken angelegter Request mit demselben action_type,
                # aber ohne die hier erwarteten Felder) - loggen statt
                # crashen, sonst bleibt die NATS-Nachricht unbestätigt und
                # wird endlos erneut zugestellt (siehe dms-eventbus-client).
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
