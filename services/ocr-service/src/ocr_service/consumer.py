import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from ocr_service import repository
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
    max_attempts: int,
) -> Callable[[bytes], Awaitable[None]]:
    """Reacts to `document.created` (first version, payload contains no
    `version_number`) and `document.version.created` (check-in, `version_
    number` in the payload) - both hook in after the scan gating from P5-S1
    (ADR 0010). Same single-subscription-with-dispatch pattern as
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
            max_attempts=max_attempts,
        )

    return handle


async def _current_batch_size(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Called freshly by `NatsEventBusClient.subscribe()` on every message
    (P5b-S5 "processing batch size") - reads the admin-UI-editable
    `OcrConfig` row live, without requiring a restart for a changed batch
    size to take effect."""
    async with session_factory() as session:
        config = await repository.get_config(session)
        await session.commit()
        return config.batch_size


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    storage: StorageClient,
    publish_event: PublishEvent,
    max_attempts: int,
) -> None:
    handler = make_handler(
        session_factory=session_factory,
        document_client=document_client,
        storage=storage,
        publish_event=publish_event,
        max_attempts=max_attempts,
    )
    for subject in subjects:
        try:
            await bus.subscribe(
                subject,
                handler,
                durable="ocr-service",
                max_concurrency=lambda: _current_batch_size(session_factory),
            )
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
