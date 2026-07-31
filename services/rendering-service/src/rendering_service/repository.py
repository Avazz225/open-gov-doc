from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rendering_service.models import Rendition


class NotFoundError(Exception):
    pass


def rendition_id(document_id: str, version_number: int, rendition_type: str) -> str:
    return f"{document_id}:{version_number}:{rendition_type}"


async def upsert_rendition(
    session: AsyncSession,
    *,
    document_id: str,
    version_number: int,
    rendition_type: str,
    source_filename: str,
    source_content_type: str | None,
    target_filename: str,
    target_content_type: str,
    size_bytes: int,
    storage_object_key: str,
    status: str,
    error_message: str | None,
) -> Rendition:
    """Legt eine Ersatzdarstellung/Vorschau an oder überschreibt eine bereits
    vorhandene Zeile mit demselben natürlichen Schlüssel (siehe models.py) -
    macht erneutes Verarbeiten derselben Version idempotent statt Duplikate
    anzuhäufen."""
    key = rendition_id(document_id, version_number, rendition_type)
    now = datetime.now(UTC)
    rendition = await session.get(Rendition, key)
    if rendition is None:
        rendition = Rendition(
            id=key,
            document_id=document_id,
            version_number=version_number,
            rendition_type=rendition_type,
            created_at=now,
        )
        session.add(rendition)
    rendition.source_filename = source_filename
    rendition.source_content_type = source_content_type
    rendition.target_filename = target_filename
    rendition.target_content_type = target_content_type
    rendition.size_bytes = size_bytes
    rendition.storage_object_key = storage_object_key
    rendition.status = status
    rendition.error_message = error_message
    rendition.updated_at = now
    await session.flush()
    return rendition


async def get_rendition_optional(session: AsyncSession, rendition_key: str) -> Rendition | None:
    """Wie `get_rendition()`, aber ohne `NotFoundError` - für die Prüfung im
    OCR-Nachzieheffekt (consumer.py), ob bereits eine `substitute_text`-
    Rendition existiert (z. B. durch `DocxTextExtractionRenderer` erzeugt),
    ohne Exception-Handling nur für diese einfache Existenzprüfung."""
    return await session.get(Rendition, rendition_key)


async def get_rendition(session: AsyncSession, rendition_key: str) -> Rendition:
    rendition = await session.get(Rendition, rendition_key)
    if rendition is None:
        raise NotFoundError(f"Ersatzdarstellung {rendition_key!r} unbekannt")
    return rendition


async def list_renditions(
    session: AsyncSession, *, document_id: str, version_number: int | None = None
) -> list[Rendition]:
    query = select(Rendition).where(Rendition.document_id == document_id)
    if version_number is not None:
        query = query.where(Rendition.version_number == version_number)
    result = await session.execute(query.order_by(Rendition.rendition_type))
    return list(result.scalars().all())
