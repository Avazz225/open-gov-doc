import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from search_service import repository
from search_service.document_client import DocumentServiceClient
from search_service.folder_client import FolderServiceClient
from search_service.ocr_client import OcrServiceClient
from search_service.pipeline import reindex_document
from search_service.rendering_client import RenderingServiceClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def make_document_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    folder_client: FolderServiceClient,
    ocr_client: OcrServiceClient,
    rendering_client: RenderingServiceClient,
) -> Callable[[bytes], Awaitable[None]]:
    """Reagiert auf `document.>` (3.1) - Dokument-Events sind bewusst dünn
    (siehe pipeline.py), daher wird bei jedem relevanten Event der volle
    Zustand per HTTP nachgeladen statt aus dem Event-Payload zu lesen."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        document_id = event.subject
        if document_id is None:
            return

        if event.event_type == "document.deleted":
            async with session_factory() as session:
                await repository.delete_document(session, document_id)
                await session.commit()
            return

        if event.event_type not in (
            "document.created",
            "document.version.created",
            "document.metadata.updated",
        ):
            return

        await reindex_document(
            document_id,
            session_factory=session_factory,
            document_client=document_client,
            folder_client=folder_client,
            ocr_client=ocr_client,
            rendering_client=rendering_client,
        )

    return handle


def make_text_update_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    folder_client: FolderServiceClient,
    ocr_client: OcrServiceClient,
    rendering_client: RenderingServiceClient,
) -> Callable[[bytes], Awaitable[None]]:
    """Reagiert auf `ocr.completed` und `rendering.completed`
    (`rendition_type == "substitute_text"` only) - Nachindexierung, da OCR/
    Rendering zeitlich nach dem initialen Upload-Event abschließen. Ruft
    dieselbe `reindex_document()` wie der Dokument-Handler auf (siehe deren
    Docstring zur Backfill-Race zwischen Streams)."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        document_id = event.subject
        if document_id is None:
            return

        if event.event_type == "ocr.completed":
            if event.payload.get("status") not in ("ready", "needs_review"):
                return
        elif event.event_type == "rendering.completed":
            if event.payload.get("rendition_type") != "substitute_text":
                return
            if event.payload.get("status") != "ready":
                return
        else:
            return

        await reindex_document(
            document_id,
            session_factory=session_factory,
            document_client=document_client,
            folder_client=folder_client,
            ocr_client=ocr_client,
            rendering_client=rendering_client,
        )

    return handle


async def start_consuming_documents(
    bus: NatsEventBusClient,
    subjects: list[str],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    folder_client: FolderServiceClient,
    ocr_client: OcrServiceClient,
    rendering_client: RenderingServiceClient,
) -> None:
    handler = make_document_handler(
        session_factory=session_factory,
        document_client=document_client,
        folder_client=folder_client,
        ocr_client=ocr_client,
        rendering_client=rendering_client,
    )
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="search-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )


async def start_consuming_text_updates(
    bus: NatsEventBusClient,
    subjects: list[str],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    folder_client: FolderServiceClient,
    ocr_client: OcrServiceClient,
    rendering_client: RenderingServiceClient,
) -> None:
    handler = make_text_update_handler(
        session_factory=session_factory,
        document_client=document_client,
        folder_client=folder_client,
        ocr_client=ocr_client,
        rendering_client=rendering_client,
    )
    for subject in subjects:
        try:
            # Eigener Durable-Name, getrennt vom document.>-Abo - beide laufen
            # über denselben event_bus-Client, aber auf verschiedenen Streams.
            await bus.subscribe(subject, handler, durable="search-service-text")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
