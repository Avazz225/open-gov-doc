import logging
from collections.abc import Awaitable, Callable

from ocr_service import repository
from ocr_service.document_client import DocumentNotFoundError, DocumentServiceClient
from ocr_service.engines import UnreadableDocumentError, select_engine
from ocr_service.models import OcrResult
from ocr_service.settings import Settings
from ocr_service.storage_client import StorageClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)
_settings = Settings()

PublishEvent = Callable[[str, str, dict], Awaitable[None]]


async def process_version(
    document_id: str,
    version_number: int,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    storage: StorageClient,
    publish_event: PublishEvent,
) -> OcrResult | None:
    """Wird sowohl vom NATS-Consumer (`consumer.py`, automatischer Pfad nach
    `document.created`/`document.version.created`) als auch direkt in Tests
    aufgerufen. Spiegelt `rendering_service.pipeline.process_version()`s
    Try/Except-Form exakt: beide Aufrufe an den Document Service in einem
    Block um `DocumentNotFoundError`, da ein permanent fehlendes Dokument/
    Storage-Objekt sonst über NATS-Redelivery in eine Endlosschleife liefe."""
    try:
        metadata = await document_client.get_version(document_id, version_number)
        data = await document_client.download_content(document_id, version_number)
    except DocumentNotFoundError:
        logger.warning(
            "Dokument %r Version %s nicht (mehr) verfügbar - OCR übersprungen",
            document_id,
            version_number,
        )
        return None

    try:
        engine = select_engine(
            content_type=metadata.content_type, filename=metadata.filename, data=data
        )
    except UnreadableDocumentError as exc:
        return await _persist_failure(
            document_id,
            version_number,
            engine_name="",
            error=str(exc),
            session_factory=session_factory,
            publish_event=publish_event,
        )

    if engine is None:
        return None  # Format ohne OCR-Bedarf (.docx/.pptx/Video/...) - keine Zeile, kein Event

    try:
        extraction = await engine.extract(
            data, filename=metadata.filename, content_type=metadata.content_type
        )
    except Exception as exc:  # Engine-Fehler isolieren (fremde Bibliotheken/Formate)
        logger.exception(
            "OCR-Engine %r fehlgeschlagen für %r Version %s",
            engine.engine_name,
            document_id,
            version_number,
        )
        return await _persist_failure(
            document_id,
            version_number,
            engine_name=engine.engine_name,
            error=str(exc),
            session_factory=session_factory,
            publish_event=publish_event,
        )

    page_image_storage_key = None
    if extraction.page_image is not None:
        page_image_storage_key = f"ocr/{document_id}/{version_number}/page-1.png"
        await storage.upload(
            page_image_storage_key, extraction.page_image, extraction.page_image_content_type
        )

    status = (
        "needs_review"
        if extraction.average_confidence < _settings.needs_review_confidence_threshold
        else "ready"
    )
    async with session_factory() as session:
        result = await repository.upsert_ocr_result(
            session,
            document_id=document_id,
            version_number=version_number,
            status=status,
            engine=extraction.engine,
            average_confidence=extraction.average_confidence,
            full_text=extraction.full_text,
            pages=[_page_to_dict(p) for p in extraction.pages],
            page_image_storage_key=page_image_storage_key,
            error_message=None,
        )
        await session.commit()

    await publish_event(
        "ocr.completed",
        document_id,
        {
            "version_number": version_number,
            "status": result.status,
            "engine": result.engine,
            "average_confidence": result.average_confidence,
        },
    )
    return result


async def _persist_failure(
    document_id: str,
    version_number: int,
    *,
    engine_name: str,
    error: str,
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: PublishEvent,
) -> OcrResult:
    async with session_factory() as session:
        result = await repository.upsert_ocr_result(
            session,
            document_id=document_id,
            version_number=version_number,
            status="failed",
            engine=engine_name,
            average_confidence=0.0,
            full_text="",
            pages=[],
            page_image_storage_key=None,
            error_message=error,
        )
        await session.commit()
    await publish_event(
        "ocr.failed", document_id, {"version_number": version_number, "error": error}
    )
    return result


def _page_to_dict(page) -> dict:
    return {
        "page_number": page.page_number,
        "width": page.width,
        "height": page.height,
        "words": [
            {
                "text": w.text,
                "left": w.left,
                "top": w.top,
                "width": w.width,
                "height": w.height,
                "confidence": w.confidence,
            }
            for w in page.words
        ],
    }
