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
) -> Callable[[bytes], Awaitable[None]]:
    """Reagiert auf `document.created` (erste Version, Payload enthält keine
    `version_number`) und `document.version.created` (Check-in, `version_
    number` im Payload) - beide docken *nach* dem Scan-Gating aus P5-S1 an, da
    Document Service diese Events erst nach erfolgreichem Virenscan und
    erfolgreichem Schreiben publiziert (siehe ADR 0010). Andere `document.>`-
    Events (Metadaten-Update, Löschung, Force-Unlock) lösen kein Rendering
    aus. Ein einziges breites Subject-Abo (`document.>`) statt zweier
    Einzel-Subscriptions, weil ein JetStream-Durable-Consumer-Name pro Subject
    reserviert wäre - Dispatch stattdessen im Handler (Muster aus
    permission-service/structure_consumer.py)."""

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
    """Nachzieheffekt aus P5-S3 (2.4/3.9): reagiert auf `ocr.completed` und
    erzeugt eine `substitute_text`-Rendition aus dem OCR-Volltext, sofern noch
    keine existiert (z. B. bereits durch `DocxTextExtractionRenderer` erzeugt).
    `ocr.failed`-Events liefern keinen Text und werden ignoriert."""

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
            # OCR Service nicht erreichbar (P5b-S5: ocrEnabled=false-Installationen
            # haben ihn gar nicht deployt) oder anderer HTTP-Fehler - nicht fatal,
            # sonst würde dieses `ocr.completed`-Event (aus der Zeit, als OCR noch
            # aktiv war) endlos redeliver-t, ohne je verarbeitbar zu sein.
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
            # Eigener Durable-Name, getrennt vom document.>-Abo oben - beide
            # laufen über denselben event_bus-Client, aber auf verschiedenen
            # Streams ("document" vs. "ocr").
            await bus.subscribe(subject, handler, durable="rendering-service-ocr")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - ocr-service noch nicht gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
