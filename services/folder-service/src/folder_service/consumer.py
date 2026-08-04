import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from folder_service import repository, retention_actions

logger = logging.getLogger(__name__)


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: Callable[[str, str, dict], Awaitable[None]],
) -> Callable[[bytes], Awaitable[None]]:
    """Führt eine zuvor per Vier-Augen-Prinzip (4.3) zurückgestellte
    Ordner-Zwangslöschung erst nach Genehmigung aus (5.2a, seit P7-S1b) -
    exaktes Copy-Paste-Muster von `document_service.consumer` für
    `document.force_delete` (P7-S1), hier für `folder.force_delete`."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        action_type = event.payload.get("action_type")
        if action_type != "folder.force_delete":
            return

        action_payload = event.payload.get("payload") or {}
        folder_id = action_payload.get("folder_id")
        if not folder_id:
            logger.warning(
                "permission.approval.approved für folder.force_delete ohne folder_id "
                "im payload erhalten - ignoriert: %r",
                action_payload,
            )
            return

        async with session_factory() as session:
            try:
                await repository.get_folder(session, folder_id)
            except repository.NotFoundError:
                logger.warning(
                    "Genehmigte Zwangslöschung für folder_id=%r konnte nicht ausgeführt werden "
                    "(Ordner inzwischen bereits anderweitig entfernt)",
                    folder_id,
                )
                return
            await retention_actions.execute_forced_deletion(
                session,
                folder_id,
                reason=action_payload.get("reason"),
                triggered_by=action_payload.get("triggered_by"),
            )
            await session.commit()
            await publish_event(
                "folder.force_deleted",
                folder_id,
                {
                    "reason": action_payload.get("reason"),
                    "triggered_by": action_payload.get("triggered_by"),
                },
            )

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: Callable[[str, str, dict], Awaitable[None]],
) -> None:
    handler = make_handler(session_factory, publish_event)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="folder-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
