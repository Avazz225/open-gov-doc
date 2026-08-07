import hashlib
import io
import logging
import zipfile
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from archival_service import crypto, repository, xdomea
from archival_service.clients import CaseClient, DocumentClient, StorageClient
from archival_service.keystore import KeyStore
from archival_service.models import CaseArchivalTransfer
from archival_service.pipeline import PipelineError

logger = logging.getLogger(__name__)

_MESSAGE_FILENAME = "aussonderung.xml"


def _archive_key(case_id: str, transfer_id: str, *, encrypted: bool) -> str:
    suffix = ".zip.enc" if encrypted else ".zip"
    return f"archive-case/{case_id}/{transfer_id}{suffix}"


async def discover_due_case_transfers(session: AsyncSession, case_client: CaseClient) -> int:
    """Phase 1 (5.6, seit P7-S3b): legt fuer jede faellige, geschlossene
    Umlaufmappe ohne bereits laufenden Transfer eine neue
    `CaseArchivalTransfer`-Zeile an - gleiches Muster wie
    `pipeline.discover_due_transfers`."""
    created = 0
    for case in await case_client.list_due_for_archival():
        existing = await repository.get_active_case_transfer_for_case(session, case["id"])
        if existing is not None:
            continue
        await repository.create_case_transfer(session, case["id"])
        created += 1
    return created


async def _build_package(
    case: dict,
    references: list[dict],
    *,
    document_client: DocumentClient,
) -> bytes:
    """Baut die XDOMEA-0503-Nachricht + laedt die referenzierten
    Dokumentinhalte, gepackt in eine ZIP-Datei (`aussonderung.xml` +
    `dokumente/<paketname>` je Dokumentversion, siehe xdomea.py)."""
    active_references = [
        r
        for r in references
        if r["removed_at"] is None and r["snapshot_version_number"] is not None
    ]

    documents: list[dict] = []
    contents: dict[str, bytes] = {}
    for reference in active_references:
        document_id = reference["document_id"]
        version_number = reference["snapshot_version_number"]
        version = await document_client.get_version(document_id, version_number)
        content_type = version.get("content_type")
        filename = xdomea.package_filename(document_id, version_number, content_type)
        documents.append(
            {
                "document_id": document_id,
                "version_number": version_number,
                "content_type": content_type,
                "original_filename": version.get("filename"),
                "package_filename": filename,
            }
        )
        contents[filename] = await document_client.download_version_content(
            document_id, version_number
        )

    message_xml = xdomea.build_aussonderung_message(case, documents)
    try:
        xdomea.validate_message(message_xml)
    except xdomea.ValidationError as exc:
        raise PipelineError(f"XDOMEA-Nachricht ungueltig: {exc}") from exc

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_MESSAGE_FILENAME, message_xml)
        for filename, data in contents.items():
            archive.writestr(f"dokumente/{filename}", data)
    return buffer.getvalue()


async def _advance_locked(
    session: AsyncSession,
    transfer: CaseArchivalTransfer,
    *,
    case_client: CaseClient,
    document_client: DocumentClient,
    storage_client: StorageClient,
    keystore: KeyStore,
    encryption_enabled: bool,
) -> bool:
    case = await case_client.get_case(transfer.case_id)
    references = await case_client.list_document_references(transfer.case_id)

    package = await _build_package(case, references, document_client=document_client)

    encrypted = False
    if encryption_enabled:
        key = keystore.get_key("default")
        package = crypto.encrypt(package, key)
        encrypted = True

    checksum = hashlib.sha256(package).hexdigest()
    archive_key = _archive_key(transfer.case_id, transfer.id, encrypted=encrypted)
    await storage_client.upload_archive_copy(archive_key, package, "application/zip")

    await repository.update_status(
        session,
        transfer,
        status="packaged",
        packaged_at=datetime.now(UTC),
        storage_object_key=archive_key,
        checksum_sha256=checksum,
        encrypted=encrypted,
    )
    await session.commit()
    return True


async def _advance_packaged(
    session: AsyncSession, transfer: CaseArchivalTransfer, *, storage_client: StorageClient
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
    session: AsyncSession, transfer: CaseArchivalTransfer, *, case_client: CaseClient
) -> bool:
    await case_client.mark_archived(transfer.case_id)
    await repository.update_status(
        session, transfer, status="released", released_at=datetime.now(UTC)
    )
    await session.commit()
    return True


async def advance_case_transfer(
    session: AsyncSession,
    transfer: CaseArchivalTransfer,
    *,
    case_client: CaseClient,
    document_client: DocumentClient,
    storage_client: StorageClient,
    keystore: KeyStore,
    encryption_enabled: bool,
) -> None:
    """Bewegt einen Case-Transfer um so viele Phasen weiter wie moeglich
    (`pending -> locked -> packaged -> verified -> released`) - gleiches
    Idiom wie `pipeline.advance_transfer`."""
    if transfer.status == "pending":
        await repository.update_status(
            session, transfer, status="locked", locked_at=datetime.now(UTC)
        )
        await session.commit()

    if transfer.status == "locked":
        if not await _advance_locked(
            session,
            transfer,
            case_client=case_client,
            document_client=document_client,
            storage_client=storage_client,
            keystore=keystore,
            encryption_enabled=encryption_enabled,
        ):
            return

    if transfer.status == "packaged":
        if not await _advance_packaged(session, transfer, storage_client=storage_client):
            return

    if transfer.status == "verified":
        await _advance_verified(session, transfer, case_client=case_client)


async def run_case_transfers_tick(
    session_factory,
    *,
    case_client: CaseClient,
    document_client: DocumentClient,
    storage_client: StorageClient,
    keystore: KeyStore,
    encryption_enabled: bool,
) -> None:
    async with session_factory() as session:
        await discover_due_case_transfers(session, case_client)
        await session.commit()

    async with session_factory() as session:
        for transfer in await repository.list_active_case_transfers(session):
            try:
                await advance_case_transfer(
                    session,
                    transfer,
                    case_client=case_client,
                    document_client=document_client,
                    storage_client=storage_client,
                    keystore=keystore,
                    encryption_enabled=encryption_enabled,
                )
            except Exception as exc:
                logger.exception(
                    "Umlaufmappen-Aussonderungs-Transfer %r fehlgeschlagen (Case %r).",
                    transfer.id,
                    transfer.case_id,
                )
                await session.rollback()
                async with session_factory() as failure_session:
                    failed_transfer = await repository.get_case_transfer(
                        failure_session, transfer.id
                    )
                    await repository.mark_failed(
                        failure_session, failed_transfer, error_message=str(exc)
                    )
                    await failure_session.commit()
