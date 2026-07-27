import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rendering_service import repository
from rendering_service.document_client import DocumentNotFoundError, DocumentServiceClient
from rendering_service.models import Rendition
from rendering_service.renderers import select_renderers
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
) -> list[Rendition]:
    """Wendet die Ersatzdarstellungs-Regeltabelle (2.4) auf eine Dokumentversion
    an. Wird sowohl vom NATS-Consumer (`consumer.py`, automatischer Pfad nach
    `document.created`/`document.version.created`) als auch direkt in Tests
    aufgerufen. Ein fehlschlagender Renderer blockiert die übrigen nicht -
    jede Regel wird unabhängig verarbeitet und einzeln als "failed" vermerkt,
    damit z. B. ein korruptes .docx nicht auch die Thumbnail-Erzeugung eines
    anderen Formats verhindern könnte (hier zwar dieselbe Datei, aber dasselbe
    Prinzip gilt, sobald mehrere Regeln auf ein Format zutreffen)."""
    try:
        metadata = await document_client.get_version(document_id, version_number)

        renderers = select_renderers(content_type=metadata.content_type, filename=metadata.filename)
        if not renderers:
            return []

        data = await document_client.download_content(document_id, version_number)
    except DocumentNotFoundError:
        # Permanenter Zustand (Version gelöscht, oder deren Inhalt im Storage
        # Service nicht mehr auffindbar) - anders als ein transienter
        # Netzwerk-/5xx-Fehler wird ein erneuter NATS-Redelivery-Versuch nie
        # erfolgreich sein. Hier abbrechen und die Nachricht trotzdem acken
        # (siehe consumer.py) statt eine Endlos-Redelivery-Schleife gegen den
        # Document Service auszulösen.
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
            except Exception as exc:  # Plugin-Fehler isolieren (fremde Bibliotheken/Formate)
                logger.exception(
                    "Renderer %r fehlgeschlagen für %r Version %s",
                    renderer.rendition_type,
                    document_id,
                    version_number,
                )
                rendition = await repository.upsert_rendition(
                    session,
                    document_id=document_id,
                    version_number=version_number,
                    rendition_type=renderer.rendition_type,
                    source_filename=metadata.filename,
                    source_content_type=metadata.content_type,
                    target_filename="",
                    target_content_type="",
                    size_bytes=0,
                    storage_object_key="",
                    status="failed",
                    error_message=str(exc),
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
            )
    return results


async def process_ocr_text(
    document_id: str,
    version_number: int,
    *,
    full_text: str,
    session_factory: async_sessionmaker[AsyncSession],
    storage: StorageClient,
    publish_event: PublishEvent,
) -> Rendition | None:
    """Nachzieheffekt (Konzept 3.9/2.4, P5-S2-Lücke): erzeugt eine
    substitute_text-Rendition aus dem OCR-Volltext für Dokumente, die diese
    Session mangels OCR nicht bedienen konnte (gescannte/bildbasierte
    Dokumente) - als beabsichtigter Nebeneffekt auch für PDFs mit echtem
    Textlayer, für die es bislang keine Textextraktion gab, nur die
    PDF/A-Archivkopie. Wird vom OCR-Consumer-Zweig aufgerufen (consumer.py),
    nachdem geprüft wurde, dass noch keine substitute_text-Rendition
    existiert."""
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
    )
    return rendition
