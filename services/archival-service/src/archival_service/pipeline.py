import hashlib
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from archival_service import crypto, repository
from archival_service.clients import (
    DocumentClient,
    ObjectTypeClient,
    RenderingClient,
    StorageClient,
)
from archival_service.keystore import KeyStore
from archival_service.models import ArchivalTransfer

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """Ein Konvertierungs-/Schreib-/Verifikationsschritt ist technisch
    fehlgeschlagen (5.6, Nutzervorgabe: kein stiller Original-Format-
    Fallback) - der aufrufende Poll-Tick markiert den Transfer als
    `failed` mit dieser Fehlermeldung statt eines stillen Abbruchs."""


def _archive_key(document_id: str, transfer_id: str, *, encrypted: bool) -> str:
    suffix = ".pdf.enc" if encrypted else ".pdf"
    return f"archive/{document_id}/{transfer_id}{suffix}"


async def discover_due_transfers(session: AsyncSession, document_client: DocumentClient) -> int:
    """Phase 1 (5.6): legt fuer jedes faellige Dokument ohne bereits
    laufenden Transfer eine neue `ArchivalTransfer`-Zeile an. Gibt die
    Anzahl neu angelegter Transfers zurueck (fuer Tests/Logging)."""
    created = 0
    for document in await document_client.list_due_for_archival():
        existing = await repository.get_active_transfer_for_document(session, document["id"])
        if existing is not None:
            continue
        await repository.create_transfer(session, document["id"])
        created += 1
    return created


async def _advance_locked(
    session: AsyncSession,
    transfer: ArchivalTransfer,
    *,
    document_client: DocumentClient,
    rendering_client: RenderingClient,
    object_type_client: ObjectTypeClient,
    storage_client: StorageClient,
    keystore: KeyStore,
) -> bool:
    """Sucht die vom regulaeren Rendition-Pipeline erzeugte `pdf_archive`-
    Ersatzdarstellung, verschluesselt sie optional und schreibt sie auf die
    Archiv-Ziele. Gibt `False` zurueck (kein Fortschritt, naechster Tick
    versucht es erneut), solange die Rendition noch nicht `ready` ist -
    das ist kein Fehler, nur ein Timing-Unterschied zur asynchronen
    Rendition-Erzeugung."""
    document = await document_client.get_document(transfer.document_id)
    rendition = await rendering_client.get_pdf_archive_rendition(
        document_id=transfer.document_id,
        version_number=document["current_version_number"],
    )
    if rendition is None:
        return False
    if rendition["status"] == "failed":
        raise PipelineError(f"PDF/A-Konvertierung fehlgeschlagen: {rendition.get('error_message')}")
    if rendition["status"] != "ready":
        return False

    content = await rendering_client.download_rendition_content(rendition["id"])

    encrypted = False
    object_type_id = document.get("object_type_id")
    if object_type_id is not None:
        object_type = await object_type_client.get_object_type(object_type_id)
        if object_type.get("archive_encryption_enabled"):
            key = keystore.get_key("default")
            content = crypto.encrypt(content, key)
            encrypted = True

    checksum = hashlib.sha256(content).hexdigest()
    archive_key = _archive_key(transfer.document_id, transfer.id, encrypted=encrypted)
    await storage_client.upload_archive_copy(archive_key, content, rendition["target_content_type"])

    await repository.update_status(
        session,
        transfer,
        status="copied",
        copied_at=datetime.now(UTC),
        storage_object_key=archive_key,
        checksum_sha256=checksum,
        archive_format="pdf_a",
        encrypted=encrypted,
    )
    await session.commit()
    return True


async def _advance_copied(
    session: AsyncSession, transfer: ArchivalTransfer, *, storage_client: StorageClient
) -> bool:
    results = await storage_client.verify_archive_copy(transfer.storage_object_key)
    if not results or not all(r["ok"] for r in results):
        raise PipelineError(f"Archiv-Kopie-Verifikation fehlgeschlagen: {results}")
    await repository.update_status(
        session, transfer, status="verified", verified_at=datetime.now(UTC)
    )
    await session.commit()
    return True


async def _advance_verified(
    session: AsyncSession, transfer: ArchivalTransfer, *, document_client: DocumentClient
) -> bool:
    await document_client.mark_archived(
        transfer.document_id, archive_format=transfer.archive_format
    )
    await repository.update_status(
        session, transfer, status="released", released_at=datetime.now(UTC)
    )
    await session.commit()
    return True


async def advance_transfer(
    session: AsyncSession,
    transfer: ArchivalTransfer,
    *,
    document_client: DocumentClient,
    rendering_client: RenderingClient,
    object_type_client: ObjectTypeClient,
    storage_client: StorageClient,
    keystore: KeyStore,
) -> None:
    """Bewegt einen Transfer um so viele Phasen weiter, wie in diesem Tick
    moeglich sind (jede Phase wird einzeln committet - ein Absturz mitten
    im Ablauf wird beim naechsten Tick am persistierten `status`
    fortgesetzt). Wirft `PipelineError` bei einem technischen
    Fehlschlag - der Aufrufer faengt das ab und markiert den Transfer als
    `failed`."""
    if transfer.status == "pending":
        await repository.update_status(
            session, transfer, status="locked", locked_at=datetime.now(UTC)
        )
        await session.commit()

    if transfer.status == "locked":
        progressed = await _advance_locked(
            session,
            transfer,
            document_client=document_client,
            rendering_client=rendering_client,
            object_type_client=object_type_client,
            storage_client=storage_client,
            keystore=keystore,
        )
        if not progressed:
            return

    if transfer.status == "copied":
        if not await _advance_copied(session, transfer, storage_client=storage_client):
            return

    if transfer.status == "verified":
        await _advance_verified(session, transfer, document_client=document_client)


async def run_active_transfers_tick(
    session_factory,
    *,
    document_client: DocumentClient,
    rendering_client: RenderingClient,
    object_type_client: ObjectTypeClient,
    storage_client: StorageClient,
    keystore: KeyStore,
) -> None:
    async with session_factory() as session:
        await discover_due_transfers(session, document_client)
        await session.commit()

    async with session_factory() as session:
        for transfer in await repository.list_active_transfers(session):
            try:
                await advance_transfer(
                    session,
                    transfer,
                    document_client=document_client,
                    rendering_client=rendering_client,
                    object_type_client=object_type_client,
                    storage_client=storage_client,
                    keystore=keystore,
                )
            except Exception as exc:
                logger.exception(
                    "Aussonderungs-Transfer %r fehlgeschlagen (Dokument %r).",
                    transfer.id,
                    transfer.document_id,
                )
                await session.rollback()
                async with session_factory() as failure_session:
                    failed_transfer = await repository.get_transfer(failure_session, transfer.id)
                    await repository.mark_failed(
                        failure_session, failed_transfer, error_message=str(exc)
                    )
                    await failure_session.commit()


async def run_dehydration_tick(
    session_factory,
    *,
    document_client: DocumentClient,
    storage_client: StorageClient,
    delay_days: int,
) -> None:
    """Phase "Dehydrieren" (5.6): entfernt die Live-Speicherkopie eines
    laengst archivierten Dokuments nach Ablauf der Uebergangsfrist - blockiert
    durch einen aktiven Legal Hold (5.2), genau wie Papierkorb/
    Zwangsloeschung."""
    async with session_factory() as session:
        due = await repository.list_due_for_dehydration(session, delay_days=delay_days)

    for transfer in due:
        try:
            if await document_client.has_active_hold(transfer.document_id):
                continue
            document = await document_client.get_document(transfer.document_id)
            version = await document_client.get_version(
                transfer.document_id, document["current_version_number"]
            )
            await storage_client.delete_live_copies(version["storage_object_key"])
            await document_client.mark_dehydrated(transfer.document_id)
            async with session_factory() as session:
                fresh = await repository.get_transfer(session, transfer.id)
                await repository.update_status(
                    session, fresh, status="dehydrated", dehydrated_at=datetime.now(UTC)
                )
                await session.commit()
        except Exception:
            logger.exception(
                "Dehydrierung fuer Transfer %r (Dokument %r) fehlgeschlagen - "
                "wird beim naechsten Tick erneut versucht.",
                transfer.id,
                transfer.document_id,
            )
