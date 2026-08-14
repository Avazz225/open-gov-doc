import logging
from collections.abc import Awaitable, Callable

from ocr_service import repository
from ocr_service.document_client import DocumentNotFoundError, DocumentServiceClient
from ocr_service.engines import UnreadableDocumentError, estimate_word_count, select_engine
from ocr_service.models import OcrResult
from ocr_service.settings import Settings
from ocr_service.storage_client import StorageClient
from ocr_service.text_layer import embed_text_layer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)
_settings = Settings()

PublishEvent = Callable[[str, str, dict], Awaitable[None]]

# Actor identifier for versions created by the OCR Service itself (text
# layer embedding, see below) - also the basis for detecting such a version
# so it is not embedded a second time (see comment there).
OCR_SERVICE_ACTOR = "system:ocr-service"


async def process_version(
    document_id: str,
    version_number: int,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    storage: StorageClient,
    publish_event: PublishEvent,
    max_attempts: int,
) -> OcrResult | None:
    """Called both by the NATS consumer (`consumer.py`, automatic path after
    `document.created`/`document.version.created`) and directly in tests.
    Exactly mirrors `rendering_service.pipeline.process_version()`'s
    try/except shape: both calls to the Document Service in one block
    around `DocumentNotFoundError`, since a permanently missing
    document/storage object would otherwise loop endlessly via NATS
    redelivery."""
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
            max_attempts=max_attempts,
        )

    if engine is None:
        return None  # format with no OCR need (.docx/.pptx/video/...) - no row, no event

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
    except Exception as exc:  # isolate engine errors (third-party libraries/formats)
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
            max_attempts=max_attempts,
        )

    # `page_image_storage_key` is a prefix, no longer a complete object ID -
    # the actual objects are stored as `{prefix}-{page}.png`, one file per
    # page (bugfix: multi-page PDFs used to always show only page 1, because
    # only "page-1.png" was written here).
    page_image_storage_key = None
    if extraction.page_images:
        page_image_storage_key = f"ocr/{document_id}/{version_number}/page"
        for page_number, page_image in enumerate(extraction.page_images, start=1):
            await storage.upload(
                f"{page_image_storage_key}-{page_number}.png",
                page_image,
                extraction.page_image_content_type,
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

    # Searchable PDF instead of a plain scan (user feedback): only if
    # Tesseract actually ran (the PDF had no usable text layer yet), the
    # source is really a PDF (there is no PDF-text-layer concept for
    # images, they instead keep their image format, see PreviewPane.tsx),
    # Tesseract recognized at least one word (a completely empty page would
    # otherwise get a pointless new version with an empty text layer - found
    # in real testing: an empty test fixture PDF unnecessarily created new
    # versions and interfered with version-number assumptions in other
    # services' tests) - AND this version was not already produced by the
    # OCR Service itself. The last point is mandatory, not just an
    # optimization: without it, the service's own new version would be
    # processed again, recognized again by Tesseract (the embedded,
    # invisible words don't change the pixels Tesseract sees;
    # `_native_text_available()`'s rough character threshold may not yet
    # trigger for short recognized text) and embedded again - observed in
    # real testing: a single short word needed two passes before enough
    # text had accumulated to reach the threshold, and would have kept
    # versioning indefinitely without this guard. Non-blocking: a failure
    # here must not invalidate the already-successful OCR finding for the
    # original version.
    is_pdf_source = (
        metadata.content_type == "application/pdf" or metadata.filename.lower().endswith(".pdf")
    )
    has_recognized_words = any(page.words for page in extraction.pages)
    is_own_previous_version = metadata.created_by == OCR_SERVICE_ACTOR
    if (
        engine.engine_name == "tesseract"
        and is_pdf_source
        and has_recognized_words
        and not is_own_previous_version
    ):
        try:
            new_pdf_bytes = embed_text_layer(data, extraction.pages, _settings.raster_dpi)
            await document_client.create_version(
                document_id,
                expected_base_version_number=version_number,
                data=new_pdf_bytes,
                filename=metadata.filename,
                content_type="application/pdf",
                created_by=OCR_SERVICE_ACTOR,
                comment="OCR: durchsuchbarer Textlayer eingebettet",
            )
        except Exception:
            logger.exception(
                "Textlayer-Einbettung/Versionierung fehlgeschlagen für %r Version %s - "
                "OCR-Ergebnis für die Originalversion bleibt trotzdem gültig",
                document_id,
                version_number,
            )

    await publish_event(
        "ocr.completed",
        document_id,
        {
            "version_number": version_number,
            "status": result.status,
            "engine": result.engine,
            "average_confidence": result.average_confidence,
        },
        actor="system:ocr-service",
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
    max_attempts: int,
) -> OcrResult:
    """Post-Roadmap Phase 20 Session 4 (ADR 0080): uses `repository.
    record_failure` instead of a direct `upsert_ocr_result` call - stays at
    `status="failed"` (retry-capable) below `max_attempts`, only switches to
    `failed_permanent` once exhausted."""
    async with session_factory() as session:
        result = await repository.record_failure(
            session,
            document_id=document_id,
            version_number=version_number,
            engine=engine_name,
            error=error,
            max_attempts=max_attempts,
        )
        await session.commit()
    await publish_event(
        "ocr.failed",
        document_id,
        {"version_number": version_number, "error": error, "status": result.status},
        actor="system:ocr-service",
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
    """Dedicated status instead of `failed` - processing skipped due to the
    configured word limit (3.9) is not an error, but a deliberate decision
    visible in the audit trail (otherwise indistinguishable from "not yet
    processed")."""
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
        actor="system:ocr-service",
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
    """Dedicated status instead of `failed`/no row - a content type not
    covered by the admin-configured allowlist (P5d-S1) is, like the word
    limit (`_persist_skip`), a deliberate decision visible in the audit
    trail, not a technical error."""
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
        actor="system:ocr-service",
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
