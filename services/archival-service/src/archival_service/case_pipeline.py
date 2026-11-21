import hashlib
import io
import logging
import zipfile
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from archival_service import crypto, repository, xdomea
from archival_service.clients import CaseClient, DocumentClient, StorageClient
from archival_service.keystore import KeyStore
from archival_service.models import CaseArchivalTransfer, CaseDestruction
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
    max_attempts: int,
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
                        failure_session,
                        failed_transfer,
                        error_message=str(exc),
                        max_attempts=max_attempts,
                    )
                    await failure_session.commit()


# --- Destruction after retention (P89-S2, ADR 0235) -------------------------


async def discover_due_case_destructions(session: AsyncSession, case_client: CaseClient) -> int:
    """Phase 1: a run per due case without an open one - same shape as
    `discover_due_case_transfers`."""
    created = 0
    for case in await case_client.list_due_for_destruction():
        if await repository.get_open_case_destruction(session, case["id"]) is not None:
            continue
        await repository.create_case_destruction(
            session, case["id"], vorgangsnummer=case.get("vorgangsnummer")
        )
        created += 1
    return created


def _mark_destroyed(destruction: CaseDestruction, *, mode, reference_count, destroyed_at) -> None:
    destruction.status = "destroyed"
    destruction.mode = mode
    if reference_count is not None:
        destruction.document_reference_count = reference_count
    destruction.destroyed_at = destroyed_at
    destruction.error_message = None
    destruction.next_retry_at = None
    destruction.updated_at = datetime.now(UTC)


async def _explain_conflict(
    destruction: CaseDestruction, detail: str, *, case_client: CaseClient
) -> None:
    """case-service answers `409` for three different reasons; the case's
    current state tells them apart without parsing the message:
    - gone or a tombstone: it was destroyed already - a previous tick's
      destroy went through but this row wasn't updated (a crash in between);
    - no longer due (retention extended, not archived): `cancelled`;
    - otherwise: a referenced document is in active quarantine - `blocked`."""
    case = await case_client.find_case(destruction.case_id)
    now = datetime.now(UTC)
    if case is None:
        _mark_destroyed(destruction, mode="deleted", reference_count=None, destroyed_at=now)
        return
    if case.get("destroyed_at"):
        _mark_destroyed(
            destruction,
            mode="tombstone",
            reference_count=None,
            destroyed_at=datetime.fromisoformat(case["destroyed_at"]),
        )
        return
    retention_until = case.get("retention_until")
    due = (
        case.get("archived_at") is not None
        and retention_until is not None
        and datetime.fromisoformat(retention_until) <= now
    )
    destruction.status = "blocked" if due else "cancelled"
    destruction.error_message = detail
    destruction.updated_at = now


async def advance_case_destruction(
    session: AsyncSession, destruction: CaseDestruction, *, case_client: CaseClient
) -> None:
    """Phase 2: asks case-service to destroy. Technical failures raise and are
    backed off by the tick; a refusal is a state, not a failure."""
    response = await case_client.destroy_case(destruction.case_id)
    if response.status_code == 200:
        body = response.json()
        _mark_destroyed(
            destruction,
            mode=body["mode"],
            reference_count=body["document_reference_count"],
            destroyed_at=datetime.fromisoformat(body["destroyed_at"]),
        )
        destruction.vorgangsnummer = body.get("vorgangsnummer") or destruction.vorgangsnummer
    elif response.status_code == 409:
        detail = response.json().get("detail", response.text)
        await _explain_conflict(destruction, str(detail), case_client=case_client)
    elif response.status_code == 404:
        # Gone before the first attempt - nothing left to destroy.
        _mark_destroyed(
            destruction, mode="deleted", reference_count=None, destroyed_at=datetime.now(UTC)
        )
    else:
        raise PipelineError(f"Vernichtung abgelehnt ({response.status_code}): {response.text}")
    await session.commit()


async def run_case_destructions_tick(
    session_factory, *, case_client: CaseClient, max_attempts: int
) -> None:
    async with session_factory() as session:
        await discover_due_case_destructions(session, case_client)
        await session.commit()

    async with session_factory() as session:
        for destruction in await repository.list_active_case_destructions(session):
            # Read before the attempt: a rollback expires the instance, and a
            # lazy reload afterwards isn't possible in async SQLAlchemy.
            destruction_id, case_id = destruction.id, destruction.case_id
            try:
                await advance_case_destruction(session, destruction, case_client=case_client)
            except Exception as exc:
                logger.exception(
                    "Vernichtung der Umlaufmappe %r fehlgeschlagen (Lauf %r).",
                    case_id,
                    destruction_id,
                )
                await session.rollback()
                async with session_factory() as failure_session:
                    failed = await repository.get_case_destruction(failure_session, destruction_id)
                    # A blocked run that now fails technically is pending
                    # again: the backoff applies to it like to any other.
                    failed.status = "pending"
                    await repository.mark_failed(
                        failure_session,
                        failed,
                        error_message=str(exc),
                        max_attempts=max_attempts,
                    )
                    await failure_session.commit()
