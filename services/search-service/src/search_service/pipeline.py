import logging
from datetime import datetime

from search_service import repository
from search_service.document_client import DocumentServiceClient
from search_service.folder_client import FolderServiceClient
from search_service.ocr_client import OcrServiceClient
from search_service.rendering_client import RenderingServiceClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


async def reindex_document(
    document_id: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    folder_client: FolderServiceClient,
    ocr_client: OcrServiceClient,
    rendering_client: RenderingServiceClient,
) -> None:
    """Rebuilds a document's search index entry from scratch - called both by
    `document.>` events (metadata changes) and by `ocr.completed`/
    `rendering.completed` (full-text follow-up delivery). A single code path
    instead of two separate "create"/"update text" variants: `document.>`
    and `ocr.>`/`rendering.>` are separate NATS streams with no ordering
    guarantee during the initial backfill run - an `ocr.completed` can
    arrive before the corresponding `document.created`. Since the full
    document state is always reloaded here via HTTP, it doesn't matter which
    event arrives first."""
    doc = await document_client.get(document_id)
    if doc is None or doc.get("deleted_at") is not None:
        async with session_factory() as session:
            await repository.delete_document(session, document_id)
            await session.commit()
        return

    folder_name = None
    if doc.get("folder_id"):
        folder = await folder_client.get(doc["folder_id"])
        folder_name = folder["name"] if folder else None

    version_number = doc["current_version_number"]

    async with session_factory() as session:
        existing = await repository.get_document(session, document_id)
        # Keep the text of an already-indexed, still-current version,
        # otherwise discard it (a new version has no OCR/rendering text of
        # its own yet until the respective events arrive).
        full_text = (
            existing.full_text
            if existing is not None and existing.current_version_number == version_number
            else ""
        )

        if version_number > 0:
            try:
                text_from_ocr = await ocr_client.get_full_text(document_id, version_number)
            except Exception:  # OCR service unreachable/no result - not fatal
                logger.exception(
                    "OCR-Abfrage fehlgeschlagen für %r Version %s", document_id, version_number
                )
                text_from_ocr = None
            if text_from_ocr:
                full_text = text_from_ocr
            elif not full_text:
                try:
                    text_from_rendering = await rendering_client.get_substitute_text(
                        document_id, version_number
                    )
                except Exception:
                    logger.exception(
                        "Rendering-Abfrage fehlgeschlagen für %r Version %s",
                        document_id,
                        version_number,
                    )
                    text_from_rendering = None
                if text_from_rendering:
                    full_text = text_from_rendering

        await repository.upsert_document(
            session,
            document_id=document_id,
            title=doc["title"],
            folder_id=doc.get("folder_id"),
            folder_name=folder_name,
            object_type_id=doc.get("object_type_id"),
            attributes=doc.get("attributes") or {},
            current_version_number=version_number,
            full_text=full_text,
            created_by=doc["created_by"],
            created_at=_parse_datetime(doc["created_at"]),
            updated_at=_parse_datetime(doc["updated_at"]),
        )
        await session.commit()


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)
