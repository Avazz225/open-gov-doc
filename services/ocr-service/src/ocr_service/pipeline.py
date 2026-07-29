import logging
from collections.abc import Awaitable, Callable

from ocr_service import repository
from ocr_service.document_client import DocumentNotFoundError, DocumentServiceClient
from ocr_service.engines import UnreadableDocumentError, estimate_word_count, select_engine
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

    async with session_factory() as session:
        config = await repository.get_config(session)
        await session.commit()

    if config.allowed_content_types and metadata.content_type not in config.allowed_content_types:
        logger.info(
            "Dokument %r Version %s hat Content-Type %r, der nicht auf der admin-konfigurierten "
            "OCR-Positivliste steht (P5d-S1) - OCR übersprungen",
            document_id,
            version_number,
            metadata.content_type,
        )
        return await _persist_content_type_skip(
            document_id,
            version_number,
            content_type=metadata.content_type,
            session_factory=session_factory,
            publish_event=publish_event,
        )

    if config.max_word_count is not None:
        estimated_words = estimate_word_count(
            data, content_type=metadata.content_type, filename=metadata.filename
        )
        if estimated_words > config.max_word_count:
            logger.info(
                "Dokument %r Version %s übersteigt geschätzt %s Wörter (Obergrenze %s) - "
                "OCR übersprungen (3.9 Kosten-/Performance-Schutzventil)",
                document_id,
                version_number,
                estimated_words,
                config.max_word_count,
            )
            return await _persist_skip(
                document_id,
                version_number,
                estimated_words=estimated_words,
                max_word_count=config.max_word_count,
                session_factory=session_factory,
                publish_event=publish_event,
            )

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


async def _persist_skip(
    document_id: str,
    version_number: int,
    *,
    estimated_words: int,
    max_word_count: int,
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: PublishEvent,
) -> OcrResult:
    """Eigener Status statt `failed` - eine übersprungene Verarbeitung wegen
    der konfigurierten Wortobergrenze (3.9) ist kein Fehler, sondern eine
    bewusste, im Audit-Trail sichtbare Entscheidung (sonst nicht von einem
    "noch nicht verarbeitet" unterscheidbar)."""
    error = (
        f"Übersprungen: geschätzt {estimated_words} Wörter über der konfigurierten "
        f"Obergrenze von {max_word_count}"
    )
    async with session_factory() as session:
        result = await repository.upsert_ocr_result(
            session,
            document_id=document_id,
            version_number=version_number,
            status="skipped",
            engine="",
            average_confidence=0.0,
            full_text="",
            pages=[],
            page_image_storage_key=None,
            error_message=error,
        )
        await session.commit()
    await publish_event(
        "ocr.skipped",
        document_id,
        {"version_number": version_number, "estimated_words": estimated_words},
    )
    return result


async def _persist_content_type_skip(
    document_id: str,
    version_number: int,
    *,
    content_type: str | None,
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: PublishEvent,
) -> OcrResult:
    """Eigener Status statt `failed`/keine Zeile - ein per admin-konfigurierter
    Positivliste nicht abgedeckter Content-Type (P5d-S1) ist wie die
    Wortobergrenze (`_persist_skip`) eine bewusste, im Audit-Trail sichtbare
    Entscheidung, kein technischer Fehler."""
    error = (
        f"Übersprungen: Content-Type {content_type!r} steht nicht auf der "
        "admin-konfigurierten OCR-Positivliste"
    )
    async with session_factory() as session:
        result = await repository.upsert_ocr_result(
            session,
            document_id=document_id,
            version_number=version_number,
            status="skipped",
            engine="",
            average_confidence=0.0,
            full_text="",
            pages=[],
            page_image_storage_key=None,
            error_message=error,
        )
        await session.commit()
    await publish_event(
        "ocr.skipped",
        document_id,
        {"version_number": version_number, "content_type": content_type},
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
