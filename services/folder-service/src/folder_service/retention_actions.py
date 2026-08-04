from sqlalchemy.ext.asyncio import AsyncSession

from folder_service import repository


async def execute_forced_deletion(
    session: AsyncSession, folder_id: str, *, reason: str | None, triggered_by: str | None
) -> None:
    """Physische Zwangslöschung eines (bereits leeren) Ordners (5.2a, seit
    P7-S1b) - im Unterschied zu `document_service.retention_actions` kein
    Storage-Bezug, da Ordner keinen eigenen Inhalt haben."""
    await repository.create_deletion_register_entry(
        session, folder_id, trigger="forced_deletion", reason=reason, triggered_by=triggered_by
    )
    await repository.hard_delete_folder(session, folder_id)


async def purge_expired_trash_entry(session: AsyncSession, folder_id: str) -> None:
    """Routinemäßige Papierkorb-Bereinigung nach Ablauf der
    Wiederherstellungsfrist (5.2, seit P7-S1b)."""
    await repository.create_deletion_register_entry(
        session, folder_id, trigger="trash_expiry", reason=None, triggered_by=None
    )
    await repository.hard_delete_folder(session, folder_id)
