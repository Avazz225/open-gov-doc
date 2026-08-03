import logging

from sqlalchemy.ext.asyncio import AsyncSession

from document_service import repository
from document_service.storage_client import DeletionBlockedError, StorageClient

logger = logging.getLogger(__name__)


async def _delete_all_versions_from_storage(
    session: AsyncSession,
    storage: StorageClient,
    document_id: str,
    *,
    bypass_governance: bool,
    x_dms_roles: str,
) -> None:
    for version in await repository.list_versions(session, document_id):
        await storage.delete(
            version.storage_object_key,
            bypass_governance=bypass_governance,
            x_dms_roles=x_dms_roles,
        )


async def execute_forced_deletion(
    session: AsyncSession,
    storage: StorageClient,
    document_id: str,
    *,
    reason: str | None,
    triggered_by: str | None,
    governance_bypass_role: str,
) -> None:
    """Physische Zwangslöschung (5.2a) - eine aktive Governance-Mode-Sperre
    wird hier bewusst umgangen (`bypass_governance=True`): genau das ist laut
    Konzept 5.2a der sanktionierte Ausnahmefall, für den Governance- statt
    Compliance-Mode überhaupt gewählt wurde (siehe ADR 0030). Entfernt
    anschließend Storage-Inhalte, Document-/DocumentVersion-Zeilen
    vollständig und hinterlässt nur den `DeletionRegisterEntry` als Nachweis."""
    await _delete_all_versions_from_storage(
        session,
        storage,
        document_id,
        bypass_governance=True,
        x_dms_roles=governance_bypass_role,
    )
    await repository.create_deletion_register_entry(
        session, document_id, trigger="forced_deletion", reason=reason, triggered_by=triggered_by
    )
    await repository.hard_delete_document(session, document_id)


async def purge_expired_trash_entry(
    session: AsyncSession, storage: StorageClient, document_id: str
) -> bool:
    """Routinemäßige Papierkorb-Bereinigung nach Ablauf der
    Wiederherstellungsfrist (5.2) - im Unterschied zur Zwangslöschung KEIN
    automatischer Governance-Bypass: ein unter Object-Lock stehendes
    Dokument bleibt bis zum Fristablauf blockiert und wird beim nächsten
    Durchlauf erneut versucht. Gibt zurück, ob die Bereinigung tatsächlich
    stattgefunden hat."""
    try:
        await _delete_all_versions_from_storage(
            session, storage, document_id, bypass_governance=False, x_dms_roles=""
        )
    except DeletionBlockedError:
        logger.warning(
            "Papierkorb-Bereinigung für document_id=%r durch aktive Governance-Mode-Sperre "
            "blockiert - wird beim nächsten Durchlauf erneut versucht",
            document_id,
        )
        return False
    await repository.create_deletion_register_entry(
        session, document_id, trigger="trash_expiry", reason=None, triggered_by=None
    )
    await repository.hard_delete_document(session, document_id)
    return True
