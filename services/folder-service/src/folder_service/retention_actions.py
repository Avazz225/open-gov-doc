import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from folder_service import repository

logger = logging.getLogger(__name__)


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


@dataclass
class TrashPurgeResult:
    purged: list[str] = field(default_factory=list)
    # Expired, but a subfolder row still points at it (held, or with a later
    # restore deadline): stays until that child is gone.
    deferred: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


async def purge_expired_trash(
    session: AsyncSession, *, restore_period_days: int
) -> TrashPurgeResult:
    """Permanently removes every trash entry past its restore period (P98-S1,
    ADR 0268).

    Trashing a folder gives its whole subtree the same `deleted_at`, so a
    parent and its subfolders expire together. Until P98 they were purged in
    whatever order the query returned them; a parent before its child hit
    `folder_parent_id_fkey` and aborted the whole tick, every hour again.

    Now deepest first, so the subtree goes bottom-up within one tick. A folder
    that still has a subfolder row afterwards - a child under legal hold
    (excluded by `list_expired_trash`) or one trashed later on its own - is
    deferred: a parent is only due when every child is. Each entry commits on
    its own, and a failing entry is rolled back and skipped, so it neither
    undoes nor blocks the others.
    """
    result = TrashPurgeResult()
    candidates = await repository.list_expired_trash(
        session, restore_period_days=restore_period_days
    )
    # Plain ids: a rollback below expires the ORM objects.
    ordered_ids = [f.id for f in await repository.order_deepest_first(session, candidates)]
    for folder_id in ordered_ids:
        if await repository.has_any_child_folder_row(session, folder_id):
            result.deferred.append(folder_id)
            continue
        try:
            await purge_expired_trash_entry(session, folder_id)
            await session.commit()
        except Exception:
            # One broken entry must not stop the rest (it did until P98).
            await session.rollback()
            logger.exception("trash_purge_failed", extra={"folder_id": folder_id})
            result.failed.append(folder_id)
            continue
        result.purged.append(folder_id)
    if result.deferred:
        logger.info(
            "trash_purge_deferred",
            extra={"folder_ids": result.deferred, "reason": "subfolder still present"},
        )
    return result
