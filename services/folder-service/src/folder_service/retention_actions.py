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


async def purge_expired_trash_entry(
    session: AsyncSession,
    folder_id: str,
    *,
    trigger: str = "trash_expiry",
    triggered_by: str | None = None,
) -> None:
    """Papierkorb-Bereinigung (5.2, seit P7-S1b) - routinemäßig nach Ablauf
    der Wiederherstellungsfrist (`trigger="trash_expiry"`, Default, vom
    `_retention_poll_loop` aufgerufen) ODER manuell auf Abruf durch die
    Löschadministration (2.5, P15-S1, `trigger="manual_purge"` mit echtem
    `triggered_by`) - identische Ausführung, nur das Löschregister-Trigger-
    Feld unterscheidet sich."""
    await repository.create_deletion_register_entry(
        session, folder_id, trigger=trigger, reason=None, triggered_by=triggered_by
    )
    await repository.hard_delete_folder(session, folder_id)
