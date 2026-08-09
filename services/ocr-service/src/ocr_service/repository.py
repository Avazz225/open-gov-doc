from datetime import UTC, datetime

from ocr_service.models import OcrConfig, OcrResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_BATCH_SIZE = 4
_CONFIG_ID = 1


class NotFoundError(Exception):
    pass


def ocr_result_id(document_id: str, version_number: int) -> str:
    return f"{document_id}:{version_number}"


async def upsert_ocr_result(
    session: AsyncSession,
    *,
    document_id: str,
    version_number: int,
    status: str,
    engine: str,
    average_confidence: float,
    full_text: str,
    pages: list,
    page_image_storage_key: str | None,
    error_message: str | None,
) -> OcrResult:
    """Legt ein OCR-Ergebnis an oder überschreibt eine bereits vorhandene Zeile
    mit demselben natürlichen Schlüssel (siehe models.py) - macht erneutes
    Verarbeiten derselben Version idempotent statt Duplikate anzuhäufen."""
    key = ocr_result_id(document_id, version_number)
    now = datetime.now(UTC)
    result = await session.get(OcrResult, key)
    if result is None:
        result = OcrResult(
            id=key, document_id=document_id, version_number=version_number, created_at=now
        )
        session.add(result)
    result.status = status
    result.engine = engine
    result.average_confidence = average_confidence
    result.full_text = full_text
    result.pages = pages
    result.page_image_storage_key = page_image_storage_key
    result.error_message = error_message
    result.updated_at = now
    await session.flush()
    return result


async def get_ocr_result(session: AsyncSession, ocr_result_key: str) -> OcrResult:
    result = await session.get(OcrResult, ocr_result_key)
    if result is None:
        raise NotFoundError(f"OCR-Ergebnis {ocr_result_key!r} unbekannt")
    return result


async def list_ocr_results(
    session: AsyncSession, *, document_id: str, version_number: int | None = None
) -> list[OcrResult]:
    query = select(OcrResult).where(OcrResult.document_id == document_id)
    if version_number is not None:
        query = query.where(OcrResult.version_number == version_number)
    result = await session.execute(query.order_by(OcrResult.version_number))
    return list(result.scalars().all())


async def get_config(session: AsyncSession) -> OcrConfig:
    """Liest die (einzige) Konfigurationszeile, legt sie mit Defaults an, falls
    sie noch nie gespeichert wurde (frischer Service, vor dem ersten `PUT
    /config`) - macht ein separates Migrations-/Seed-Skript überflüssig."""
    config = await session.get(OcrConfig, _CONFIG_ID)
    if config is None:
        config = OcrConfig(
            id=_CONFIG_ID,
            max_word_count=None,
            batch_size=DEFAULT_BATCH_SIZE,
            # Standardmäßig nur PDFs (Nutzer-Feedback) - Rasterbilder erfordern
            # eine bewusste Admin-Freigabe über PUT /config, kein automatischer
            # OCR-Lauf auf jedes hochgeladene Bild.
            allowed_content_types=["application/pdf"],
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_config(
    session: AsyncSession,
    *,
    max_word_count: int | None,
    batch_size: int,
    allowed_content_types: list[str],
) -> OcrConfig:
    config = await get_config(session)
    config.max_word_count = max_word_count
    config.batch_size = batch_size
    config.allowed_content_types = allowed_content_types
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config
