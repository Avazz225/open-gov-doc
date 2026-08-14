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
    """Physical forced deletion (5.2a) - an active governance-mode lock
    is deliberately bypassed here (`bypass_governance=True`): according to
    concept 5.2a, this is exactly the sanctioned exception case for which
    governance mode was chosen over compliance mode in the first place (see
    ADR 0030). Subsequently removes storage content and Document/
    DocumentVersion rows completely, leaving only the `DeletionRegisterEntry`
    as evidence."""
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
    session: AsyncSession,
    storage: StorageClient,
    document_id: str,
    *,
    trigger: str = "trash_expiry",
    triggered_by: str | None = None,
) -> bool:
    """Trash cleanup (5.2) - routinely after the restoration period
    expires (`trigger="trash_expiry"`, default, called by
    `_retention_poll_loop`) OR manually on demand by the deletion
    administration (2.5, P15-S1, `trigger="manual_purge"` with a real
    `triggered_by`) - identical execution, only the trigger/the deletion
    register trigger field differs. Unlike forced deletion, NO automatic
    governance bypass: a document under object lock remains blocked (on the
    automatic path until the next run, on the manual path the caller
    reports this to the endpoint caller as 409). Returns whether the
    cleanup actually took place."""
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
        session, document_id, trigger=trigger, reason=None, triggered_by=triggered_by
    )
    await repository.hard_delete_document(session, document_id)
    return True
