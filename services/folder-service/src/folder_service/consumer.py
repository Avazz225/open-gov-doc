import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from folder_service import repository, retention_actions
from folder_service.document_client import DocumentClient

logger = logging.getLogger(__name__)


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    document_client: DocumentClient | None = None,
) -> Callable[[bytes], Awaitable[None]]:
    """Führt zuvor per Vier-Augen-Prinzip (4.3) zurückgestellte Aktionen erst
    nach Genehmigung aus: Ordner-Zwangslöschung (5.2a, seit P7-S1b,
    `folder.force_delete` - exaktes Copy-Paste-Muster von `document_service.
    consumer` für `document.force_delete`, P7-S1) und seit P7-S1c
    Löschantrag für reguläre Nutzer (5.2, `folder.delete`). `document_client`
    ist nur für Letzteres nötig (Kaskade auf enthaltene Dokumente, siehe
    `repository.soft_delete_folder`) - optional, damit bestehende Aufrufer
    ohne den neuen Parameter weiterhin funktionieren."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        action_type = event.payload.get("action_type")
        if action_type == "folder.delete":
            await _handle_delete_approved(session_factory, publish_event, document_client, event)
            return
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


async def _handle_delete_approved(
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    document_client: DocumentClient | None,
    event: Event,
) -> None:
    """Löschantrag-Workflow für reguläre Nutzer (5.2, seit P7-S1c) - führt
    eine zuvor per Vier-Augen-Prinzip zurückgestellte reguläre
    Papierkorb-Verschiebung (inkl. Kaskade auf Unterordner/Dokumente) aus,
    sobald sie genehmigt wurde."""
    action_payload = event.payload.get("payload") or {}
    folder_id = action_payload.get("folder_id")
    if not folder_id:
        logger.warning(
            "permission.approval.approved für folder.delete ohne folder_id im payload "
            "erhalten - ignoriert: %r",
            action_payload,
        )
        return
    if document_client is None:
        logger.error(
            "Genehmigter Löschantrag für folder_id=%r kann nicht ausgeführt werden - "
            "kein document_client konfiguriert",
            folder_id,
        )
        return

    async with session_factory() as session:
        deleted_by = action_payload.get("deleted_by")
        try:
            await repository.soft_delete_folder(
                session, folder_id, deleted_by=deleted_by, document_client=document_client
            )
        except repository.NotFoundError:
            logger.warning(
                "Genehmigter Löschantrag für folder_id=%r konnte nicht ausgeführt werden "
                "(Ordner inzwischen bereits anderweitig entfernt)",
                folder_id,
            )
            return
        await session.commit()
        await publish_event("folder.trashed", folder_id, {"deleted_by": deleted_by})


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    document_client: DocumentClient | None = None,
) -> None:
    handler = make_handler(session_factory, publish_event, document_client)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="folder-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
