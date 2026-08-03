import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from document_service import repository, retention_actions
from document_service.storage_client import StorageClient

logger = logging.getLogger(__name__)


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    storage: StorageClient,
    governance_bypass_role: str,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
) -> Callable[[bytes], Awaitable[None]]:
    """Führt zuvor per Vier-Augen-Prinzip (4.3, P6-S4) zurückgestellte
    Aktionen erst nach Genehmigung aus: Force-Unlock (P6-S4) und, seit
    P7-S1, Zwangslöschung (5.2a, `document.force_delete`). Andere
    Aktionstypen (z. B. Bereichssperren) gehören nicht zu diesem Service und
    werden ignoriert."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        action_type = event.payload.get("action_type")
        if action_type == "document.force_delete":
            await _handle_force_delete_approved(
                session_factory, storage, governance_bypass_role, publish_event, event
            )
            return
        if action_type != "document.force_unlock":
            return
        action_payload = event.payload.get("payload") or {}
        document_id = action_payload.get("document_id")
        if not document_id:
            # Fremd-/Fehlform-Payload (z. B. ein zu Testzwecken über
            # /approval-requests angelegter Request mit demselben
            # action_type, aber ohne document_id) - loggen statt crashen,
            # sonst bleibt die NATS-Nachricht unbestätigt und wird endlos
            # erneut zugestellt (siehe dms-eventbus-client._callback).
            logger.warning(
                "permission.approval.approved für document.force_unlock ohne document_id "
                "im payload erhalten - ignoriert: %r",
                action_payload,
            )
            return

        async with session_factory() as session:
            try:
                original_lock = await repository.force_release_lock(session, document_id)
            except repository.NotFoundError:
                logger.warning(
                    "Genehmigter Force-Unlock für document_id=%r konnte nicht ausgeführt "
                    "werden (Sperre inzwischen anderweitig aufgelöst)",
                    document_id,
                )
                return
            await session.commit()
            await publish_event(
                "document.lock.force_released",
                document_id,
                {
                    "original_locked_by": original_lock.locked_by,
                    "released_by": action_payload.get("released_by"),
                    "reason": action_payload.get("reason"),
                },
            )

    return handle


async def _handle_force_delete_approved(
    session_factory: async_sessionmaker[AsyncSession],
    storage: StorageClient,
    governance_bypass_role: str,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    action_payload = event.payload.get("payload") or {}
    document_id = action_payload.get("document_id")
    if not document_id:
        logger.warning(
            "permission.approval.approved für document.force_delete ohne document_id "
            "im payload erhalten - ignoriert: %r",
            action_payload,
        )
        return

    async with session_factory() as session:
        try:
            await repository.get_document(session, document_id)
        except repository.NotFoundError:
            logger.warning(
                "Genehmigte Zwangslöschung für document_id=%r konnte nicht ausgeführt werden "
                "(Dokument inzwischen bereits anderweitig entfernt)",
                document_id,
            )
            return
        await retention_actions.execute_forced_deletion(
            session,
            storage,
            document_id,
            reason=action_payload.get("reason"),
            triggered_by=action_payload.get("triggered_by"),
            governance_bypass_role=governance_bypass_role,
        )
        await session.commit()
        await publish_event(
            "document.force_deleted",
            document_id,
            {
                "reason": action_payload.get("reason"),
                "triggered_by": action_payload.get("triggered_by"),
            },
        )


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
    storage: StorageClient,
    governance_bypass_role: str,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
) -> None:
    handler = make_handler(session_factory, storage, governance_bypass_role, publish_event)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="document-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
