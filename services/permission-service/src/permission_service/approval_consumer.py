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
    """Führt die eigenen gegateten Aktionen (Bereichssperren, 4.7) erst nach
    Genehmigung aus (4.3) - Selbst-Konsum des eigenen
    `permission.approval.approved`-Events, exakt derselbe Mechanismus wie
    für fremde Services (z. B. `document-service`). Aktionstypen, die nicht
    zu diesem Service gehören (z. B. `document.force_unlock`), werden
    ignoriert."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        action_type = event.payload.get("action_type")
        if action_type not in ("permission.scope_lock.create", "permission.scope_lock.release"):
            return
        action_payload = event.payload.get("payload") or {}

        async with session_factory() as session:
            try:
                if action_type == "permission.scope_lock.create":
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
                    )
                else:
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
