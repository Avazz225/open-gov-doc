from sqlalchemy.ext.asyncio import AsyncSession

from folder_service import repository


async def execute_forced_deletion(
    session: AsyncSession, folder_id: str, *, reason: str | None, triggered_by: str | None
) -> None:
    """Physical forced deletion of an (already empty) folder (5.2a, since
    P7-S1b) - unlike `document_service.retention_actions`, no storage
    involvement, since folders have no content of their own."""
    await repository.create_deletion_register_entry(
        session, folder_id, trigger="forced_deletion", reason=reason, triggered_by=triggered_by
    )
    await repository.hard_delete_folder(session, folder_id)


async def purge_expired_trash_entry(
    session: AsyncSession,
    folder_id: str,
    *,
    trigger: str = "trash_expiry",
    triggered_by: str | None = None,
) -> None:
    """Trash cleanup (5.2, since P7-S1b) - routinely after the restore
    period expires (`trigger="trash_expiry"`, default, called by
    `_retention_poll_loop`) OR manually on demand by deletion
    administration (2.5, P15-S1, `trigger="manual_purge"` with a real
    `triggered_by`) - identical execution, only the deletion-register
    trigger field differs."""
    await repository.create_deletion_register_entry(
        session, folder_id, trigger=trigger, reason=None, triggered_by=triggered_by
    )
    await repository.hard_delete_folder(session, folder_id)
