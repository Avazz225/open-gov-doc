import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rendering_service import repository
from rendering_service.document_client import DocumentNotFoundError, DocumentServiceClient
from rendering_service.models import Rendition
from rendering_service.renderers import get_renderer_by_type, select_renderers
from rendering_service.storage_client import StorageClient

logger = logging.getLogger(__name__)

PublishEvent = Callable[[str, str, dict], Awaitable[None]]


async def process_version(
    document_id: str,
    version_number: int,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    storage: StorageClient,
    publish_event: PublishEvent,
    max_attempts: int,
) -> list[Rendition]:
    """Applies the rendition rule table (2.4) to a document version. Called
    both by the NATS consumer (`consumer.py`, automatic path after
    `document.created`/`document.version.created`) and directly in tests. A
    failing renderer does not block the others - each rule is processed
    independently and individually marked as "failed", so that e.g. a
    corrupt .docx cannot also prevent thumbnail generation of another format
    (here it's admittedly the same file, but the same principle applies once
    several rules apply to one format)."""
    try:
        metadata = await document_client.get_version(document_id, version_number)

        renderers = select_renderers(content_type=metadata.content_type, filename=metadata.filename)
        if not renderers:
            return []

        data = await document_client.download_content(document_id, version_number)
    except DocumentNotFoundError:
        # Permanent state (version deleted, or its content no longer findable
        # in the Storage Service) - unlike a transient network/5xx error, a
        # renewed NATS redelivery attempt will never succeed. Abort here and
        # ack the message anyway (see consumer.py) instead of triggering an
        # endless redelivery loop against the Document Service.
        logger.warning(
            "Dokument %r Version %s nicht (mehr) verfügbar - Rendering übersprungen",
            document_id,
            version_number,
        )
        return []

    results: list[Rendition] = []
    async with session_factory() as session:
        for renderer in renderers:
            try:
                output = await renderer.render(
                    data, filename=metadata.filename, content_type=metadata.content_type
                )
                key = f"renditions/{document_id}/{version_number}/{output.rendition_type}"
                await storage.upload(key, output.data, output.target_content_type)
                rendition = await repository.upsert_rendition(
                    session,
                    document_id=document_id,
                    version_number=version_number,
                    rendition_type=output.rendition_type,
                    source_filename=metadata.filename,
                    source_content_type=metadata.content_type,
                    target_filename=output.target_filename,
                    target_content_type=output.target_content_type,
                    size_bytes=len(output.data),
                    storage_object_key=key,
                    status="ready",
                    error_message=None,
                )
            except Exception as exc:  # isolate plugin errors (third-party libraries/formats)
                logger.exception(
                    "Renderer %r fehlgeschlagen für %r Version %s",
                    renderer.rendition_type,
                    document_id,
                    version_number,
                )
                rendition = await repository.record_failure(
                    session,
                    document_id=document_id,
                    version_number=version_number,
                    rendition_type=renderer.rendition_type,
                    source_filename=metadata.filename,
                    source_content_type=metadata.content_type,
                    error=str(exc),
                    max_attempts=max_attempts,
                )
            await session.commit()
            results.append(rendition)
            await publish_event(
                "rendering.completed",
                document_id,
                {
                    "version_number": version_number,
                    "rendition_type": rendition.rendition_type,
                    "target_filename": rendition.target_filename,
                    "status": rendition.status,
                    "error": rendition.error_message,
                },
                actor="system:rendering-service",
            )
    return results


async def retry_rendition(
    document_id: str,
    version_number: int,
    rendition_type: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    storage: StorageClient,
    publish_event: PublishEvent,
    max_attempts: int,
) -> Rendition | None:
    """Retries specifically ONLY the one failed renderer (post-roadmap
    phase 20 session 4, ADR 0080) - unlike `process_version` (reruns all
    applicable rules), here `get_renderer_by_type` looks up exactly the
    affected renderer, so a retry does not unnecessarily regenerate already
    successful renditions too. Called from the retry poll loop AND from the
    manual `POST .../retry` endpoint (main.py)."""
    renderer = get_renderer_by_type(rendition_type)
    if renderer is None:
        logger.warning(
            "Kein Renderer mehr für rendition_type %r (Dokument %r Version %s) - "
            "Retry übersprungen",
            rendition_type,
            document_id,
            version_number,
        )
        return None

    try:
        metadata = await document_client.get_version(document_id, version_number)
        data = await document_client.download_content(document_id, version_number)
    except DocumentNotFoundError:
        logger.warning(
            "Dokument %r Version %s nicht (mehr) verfügbar - Retry übersprungen",
            document_id,
            version_number,
        )
        return None

    async with session_factory() as session:
        try:
            output = await renderer.render(
                data, filename=metadata.filename, content_type=metadata.content_type
            )
            key = f"renditions/{document_id}/{version_number}/{output.rendition_type}"
            await storage.upload(key, output.data, output.target_content_type)
            rendition = await repository.upsert_rendition(
                session,
                document_id=document_id,
                version_number=version_number,
                rendition_type=output.rendition_type,
                source_filename=metadata.filename,
                source_content_type=metadata.content_type,
                target_filename=output.target_filename,
                target_content_type=output.target_content_type,
                size_bytes=len(output.data),
                storage_object_key=key,
                status="ready",
                error_message=None,
            )
        except Exception as exc:  # isolate plugin errors, same pattern as process_version
            logger.exception(
                "Renderer %r erneut fehlgeschlagen für %r Version %s",
                renderer.rendition_type,
                document_id,
                version_number,
            )
            rendition = await repository.record_failure(
                session,
                document_id=document_id,
                version_number=version_number,
                rendition_type=renderer.rendition_type,
                source_filename=metadata.filename,
                source_content_type=metadata.content_type,
                error=str(exc),
                max_attempts=max_attempts,
            )
        await session.commit()
    await publish_event(
        "rendering.completed",
        document_id,
        {
            "version_number": version_number,
            "rendition_type": rendition.rendition_type,
            "target_filename": rendition.target_filename,
            "status": rendition.status,
            "error": rendition.error_message,
        },
        actor="system:rendering-service",
    )
    return rendition


async def process_ocr_text(
    document_id: str,
    version_number: int,
    *,
    full_text: str,
    session_factory: async_sessionmaker[AsyncSession],
    storage: StorageClient,
    publish_event: PublishEvent,
) -> Rendition | None:
    """Follow-up effect (concept 3.9/2.4, P5-S2 gap): creates a
    substitute_text rendition from the OCR full text for documents that this
    session could not serve for lack of OCR (scanned/image-based documents) -
    as an intended side effect also for PDFs with a real text layer, for
    which no text extraction previously existed, only the PDF/A archive
    copy. Called from the OCR consumer branch (consumer.py) after checking
    that no substitute_text rendition exists yet."""
    if not full_text.strip():
        return None
    key = f"renditions/{document_id}/{version_number}/substitute_text"
    data = full_text.encode("utf-8")
    await storage.upload(key, data, "text/plain; charset=utf-8")
    async with session_factory() as session:
        rendition = await repository.upsert_rendition(
            session,
            document_id=document_id,
            version_number=version_number,
            rendition_type="substitute_text",
            source_filename="",
            source_content_type=None,
            target_filename="ocr_text.txt",
            target_content_type="text/plain; charset=utf-8",
            size_bytes=len(data),
            storage_object_key=key,
            status="ready",
            error_message=None,
        )
        await session.commit()
    await publish_event(
        "rendering.completed",
        document_id,
        {
            "version_number": version_number,
            "rendition_type": "substitute_text",
            "target_filename": "ocr_text.txt",
            "status": "ready",
            "error": None,
        },
        actor="system:rendering-service",
    )
    return rendition
