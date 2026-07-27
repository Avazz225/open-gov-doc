import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from ocr_service.document_client import DocumentServiceClient
from ocr_service.pipeline import PublishEvent, process_version
from ocr_service.storage_client import StorageClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def make_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    storage: StorageClient,
    publish_event: PublishEvent,
) -> Callable[[bytes], Awaitable[None]]:
    """Reagiert auf `document.created` (erste Version, Payload enthält keine
    `version_number`) und `document.version.created` (Check-in, `version_
    number` im Payload) - beide docken nach dem Scan-Gating aus P5-S1 an
    (ADR 0010). Gleiches Ein-Abo-mit-Dispatch-Muster wie
    rendering_service/consumer.py."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.event_type == "document.created":
            version_number = 1
        elif event.event_type == "document.version.created":
            version_number = event.payload["version_number"]
        else:
            return

        document_id = event.subject
        if document_id is None:
            return

        await process_version(
            document_id,
            version_number,
            session_factory=session_factory,
            document_client=document_client,
            storage=storage,
            publish_event=publish_event,
        )

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    storage: StorageClient,
    publish_event: PublishEvent,
) -> None:
    handler = make_handler(
        session_factory=session_factory,
        document_client=document_client,
        storage=storage,
        publish_event=publish_event,
    )
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="ocr-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
