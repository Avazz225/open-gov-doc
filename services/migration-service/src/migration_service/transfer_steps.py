"""Die eigentliche fachliche Arbeit je Phase (7.2) - aufgerufen von den
`POST /transfers/{id}/steps/*`-Endpunkten in `main.py`, die ihrerseits die
`serviceUrl`-Ziele der `connector_call`-Service-Tasks in
`resources/migration_transfer.bpmn`/`migration_dry_run.bpmn` sind (P12-S2,
siehe `docs/services/workflow-service.md` "Connector-Service-Tasks").

Jede Funktion bekommt die aktuellen Prozessdaten (`data`, aus dem BPMN-Task)
und gibt ein Dict zurück, das die Workflow-Engine in die Prozessdaten
zurückmerged - Fortschritt/Statuswechsel selbst werden zusätzlich direkt in
der `transfer`-Zeile persistiert (die eigentliche, dauerhafte Quelle für
`GET /transfers/{id}`; die Workflow-Prozessdaten sind nur Plumbing zwischen
den Schritten).

Jeder Aufruf von `local`/`peer` (beide synchron, siehe `dms_client.py`s
Moduldocstring) läuft über `asyncio.to_thread()` statt direkt - ein synchroner
HTTP-Aufruf **direkt** in einem `async def`-Endpoint blockiert den gesamten
Event-Loop-Thread dieses Prozesses. Beim Selbst-Loopback-Test (dieselbe
Instanz ruft sich selbst als Ziel auf) führt das zu einem echten Deadlock: der
blockierende Aufruf wartet auf eine Antwort von genau dem Thread, den er
selbst blockiert, und der die eingehende Anfrage sonst verarbeiten würde -
real aufgetreten (`httpx.ReadTimeout`) und hier behoben. `asyncio.to_thread()`
(sync-Arbeit AUS einem async-Kontext heraus in einen Thread auslagern) ist
die unproblematische Richtung, anders als das in P12-S1 bewusst vermiedene
`asgiref.async_to_sync` (async-Aufruf AUS synchronem Code heraus)."""

import asyncio
from datetime import UTC, datetime

import httpx
from migration_service.dms_client import LocalDmsClient
from migration_service.models import Transfer
from migration_service.peer_client import PeerClient
from sqlalchemy.ext.asyncio import AsyncSession


class StepError(Exception):
    """Ein Schritt konnte nicht ausgeführt werden - wird von `main.py` als
    `error_message` auf der Transfer-Zeile hinterlegt und als Exception nach
    oben durchgereicht (löst den `ERROR`-Zustand des Service-Tasks aus, siehe
    `spiff_adapter.retry_errored_tasks` - Resumability, 7.2)."""


async def _mark(
    session: AsyncSession, transfer: Transfer, *, status: str, timestamp_field: str | None = None
) -> None:
    transfer.status = status
    transfer.updated_at = datetime.now(UTC)
    if timestamp_field is not None:
        setattr(transfer, timestamp_field, transfer.updated_at)
    await session.flush()


async def lock(session: AsyncSession, transfer: Transfer, local: LocalDmsClient) -> dict:
    scope_lock_id = await asyncio.to_thread(
        local.acquire_scope_lock,
        transfer.source_folder_id,
        locked_by="migration-service",
        reason=f"Migration-Transfer {transfer.id}",
    )
    transfer.scope_lock_id = scope_lock_id
    await _mark(session, transfer, status="locked", timestamp_field="locked_at")
    return {"scope_lock_id": scope_lock_id}


def _copy_folder_recursive(
    local: LocalDmsClient,
    peer: PeerClient,
    transfer_id: str,
    *,
    source_folder_id: str,
    target_folder_id: str,
    created_by: str,
    checksums: dict[str, str],
    progress: dict[str, int],
) -> None:
    """Läuft den Quellbaum ab `source_folder_id` rekursiv ab - kopiert
    Dokumente + legt Unterordner an der jeweils entsprechenden Stelle im
    Zielbaum an, überträgt anschließend die Rollenzuweisungen JEDES
    besuchten Ordners (7.2 "Berechtigungen"). Bewusst eine einzige,
    synchrone Funktion statt Batch-/Parallelisierung - für eine
    Referenzimplementierung ausreichend, siehe
    docs/services/migration-service.md "Bewusste Grenzen" zur Laufzeit bei
    sehr großen Bäumen."""
    for assignment in local.list_role_assignments(source_folder_id):
        peer.push_permission(
            transfer_id,
            target_resource_id=target_folder_id,
            principal_type=assignment.principal_type,
            principal_id=assignment.principal_id,
            role_name=assignment.role_name,
            role_description=assignment.role_description,
            role_permissions=assignment.role_permissions,
        )

    folders, documents = local.tree.list_children(source_folder_id)
    for document in documents:
        content = local.tree.read_document_content(document.id)
        pushed = peer.push_document(
            transfer_id,
            target_folder_id=target_folder_id,
            filename=document.title,
            content=content,
            content_type=document.content_type,
            created_by=created_by,
            existing_target_document_id=None,
            expected_base_version_number=None,
        )
        checksums[pushed["id"]] = document.checksum_sha256 or ""
        progress["documents_copied"] += 1

    for folder in folders:
        pushed_folder = peer.push_folder(
            transfer_id,
            parent_target_folder_id=target_folder_id,
            name=folder.name,
            created_by=created_by,
        )
        _copy_folder_recursive(
            local,
            peer,
            transfer_id,
            source_folder_id=folder.id,
            target_folder_id=pushed_folder["id"],
            created_by=created_by,
            checksums=checksums,
            progress=progress,
        )


async def copy(
    session: AsyncSession,
    transfer: Transfer,
    local: LocalDmsClient,
    peer: PeerClient,
    *,
    created_by: str,
) -> dict:
    source_folder = await asyncio.to_thread(local.tree.get_folder, transfer.source_folder_id)
    announced = await asyncio.to_thread(
        peer.announce_transfer,
        transfer_id=transfer.id,
        source_installation_id=transfer.id,
        target_parent_folder_id="root",
        name=source_folder.name,
    )
    target_folder_id = announced["target_folder_id"]

    checksums: dict[str, str] = {}
    progress = {"documents_copied": 0}
    await asyncio.to_thread(
        _copy_folder_recursive,
        local,
        peer,
        transfer.id,
        source_folder_id=transfer.source_folder_id,
        target_folder_id=target_folder_id,
        created_by=created_by,
        checksums=checksums,
        progress=progress,
    )

    transfer.documents_total = len(checksums)
    transfer.documents_copied = progress["documents_copied"]
    await _mark(session, transfer, status="copied", timestamp_field="copied_at")
    return {"target_folder_id": target_folder_id, "document_checksums": checksums}


async def verify(session: AsyncSession, transfer: Transfer, peer: PeerClient, data: dict) -> dict:
    checksums: dict[str, str] = data.get("document_checksums", {})
    result = await asyncio.to_thread(peer.verify, transfer.id, document_ids=list(checksums.keys()))
    remote_checksums: dict[str, str] = result.get("checksums", {})
    mismatches = [
        doc_id for doc_id, expected in checksums.items() if remote_checksums.get(doc_id) != expected
    ]
    if mismatches or len(remote_checksums) != len(checksums):
        transfer.error_message = f"Prüfsummen-Abweichung bei {len(mismatches)} Dokument(en)"
        await _mark(session, transfer, status="failed")
        raise StepError(transfer.error_message)
    transfer.documents_verified = len(checksums)
    await _mark(session, transfer, status="verified", timestamp_field="verified_at")
    return {}


async def release(
    session: AsyncSession, transfer: Transfer, local: LocalDmsClient, peer: PeerClient
) -> dict:
    await asyncio.to_thread(peer.release, transfer.id)
    if transfer.scope_lock_id is not None:
        await asyncio.to_thread(
            local.release_scope_lock, transfer.scope_lock_id, released_by="migration-service"
        )
    await _mark(session, transfer, status="released", timestamp_field="released_at")
    return {"retention_duration": f"P{transfer.retention_days}D"}


async def delete_source(
    session: AsyncSession, transfer: Transfer, *, folder_service_base_url: str
) -> dict:
    def _trash() -> dict:
        with httpx.Client(base_url=folder_service_base_url, timeout=60.0) as client:
            response = client.post(
                f"/folders/{transfer.source_folder_id}/trash",
                json={"deleted_by": "migration-service"},
            )
            response.raise_for_status()
            return response.json()

    body = await asyncio.to_thread(_trash)

    if body.get("status") != "trashed":
        # Vier-Augen (4.3) ist für `folder.delete` konfigurierbar (P7-S1c) -
        # löst genau dann einen eigenen Freigabe-Antrag bei folder-service aus,
        # außerhalb der Kontrolle dieses Service. Die Migration selbst ist zu
        # diesem Zeitpunkt bereits vollständig abgeschlossen (released) - eine
        # verzögerte Löschung im Quellsystem ist kein Fehlschlag des Transfers.
        transfer.error_message = (
            f"Löschung im Quellsystem nicht sofort ausgeführt (Status: {body.get('status')})"
        )
        await session.flush()
        return {}

    await _mark(session, transfer, status="deleted", timestamp_field="deleted_at")
    return {}


async def dry_run_check(transfer: Transfer, peer: PeerClient) -> dict:
    result = await asyncio.to_thread(peer.dry_run_check, target_parent_folder_id="root")
    return {"dry_run_result": result}
