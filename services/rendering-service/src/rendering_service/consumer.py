import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rendering_service import repository
from rendering_service.document_client import DocumentServiceClient
from rendering_service.ocr_client import OcrServiceClient
from rendering_service.pipeline import PublishEvent, process_ocr_text, process_version
from rendering_service.storage_client import StorageClient

logger = logging.getLogger(__name__)


def make_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    storage: StorageClient,
    publish_event: PublishEvent,
    max_attempts: int,
) -> Callable[[bytes], Awaitable[None]]:
    """Reacts to `document.created` (first version, payload does not contain
    `version_number`) and `document.version.created` (check-in, `version_
    number` in the payload) - both hook in *after* the scan gating from P5-S1,
    since Document Service only publishes these events after a successful
    virus scan and successful write (see ADR 0010). Other `document.>`
    events (metadata update, deletion, force unlock) do not trigger
    rendering. A single broad subject subscription (`document.>`) instead of
    two individual subscriptions, because a JetStream durable consumer name
    would be reserved per subject - dispatch happens in the handler instead
    (pattern from permission-service/structure_consumer.py)."""

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
            await bus.subscribe(subject, handler, durable="rendering-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )


def make_ocr_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    ocr_client: OcrServiceClient,
    storage: StorageClient,
    publish_event: PublishEvent,
) -> Callable[[bytes], Awaitable[None]]:
    """Follow-up effect from P5-S3 (2.4/3.9): reacts to `ocr.completed` and
    creates a `substitute_text` rendition from the OCR full text, provided
    none exists yet (e.g. already created by `DocxTextExtractionRenderer`).
    `ocr.failed` events do not carry text and are ignored."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.event_type != "ocr.completed":
            return

        document_id = event.subject
        version_number = event.payload.get("version_number")
        status = event.payload.get("status")
        if document_id is None or version_number is None or status not in ("ready", "needs_review"):
            return

        async with session_factory() as session:
            existing = await repository.get_rendition_optional(
                session, repository.rendition_id(document_id, version_number, "substitute_text")
            )
        if existing is not None:
            return

        try:
            full_text = await ocr_client.get_full_text(document_id, version_number)
        except Exception:
            # OCR Service unreachable (P5b-S5: ocrEnabled=false installations
            # don't deploy it at all) or another HTTP error - not fatal,
            # otherwise this `ocr.completed` event (from a time when OCR was
            # still active) would be redelivered endlessly without ever being
            # processable.
            logger.exception(
                "OCR-Abfrage fehlgeschlagen für %r Version %s", document_id, version_number
            )
            return
        if full_text is None:
            return

        await process_ocr_text(
            document_id,
            version_number,
            full_text=full_text,
            session_factory=session_factory,
            storage=storage,
            publish_event=publish_event,
        )

    return handle


async def start_consuming_ocr(
    bus: NatsEventBusClient,
    subjects: list[str],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    ocr_client: OcrServiceClient,
    storage: StorageClient,
    publish_event: PublishEvent,
) -> None:
    handler = make_ocr_handler(
        session_factory=session_factory,
        ocr_client=ocr_client,
        storage=storage,
        publish_event=publish_event,
    )
    for subject in subjects:
        try:
            # Own durable name, separate from the document.> subscription
            # above - both run over the same event_bus client, but on
            # different streams ("document" vs. "ocr").
            await bus.subscribe(subject, handler, durable="rendering-service-ocr")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - ocr-service noch nicht gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
