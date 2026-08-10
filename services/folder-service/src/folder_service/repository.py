import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from folder_service.document_client import DocumentClient
from folder_service.models import (
    DeletionRegisterEntry,
    Folder,
    LegalHold,
    RetentionConfig,
    TrashConfig,
)
from folder_service.settings import ROOT_FOLDER_ID

_RETENTION_CONFIG_ID = 1
_TRASH_CONFIG_ID = 1


class NotFoundError(Exception):
    pass


class FolderNotEmptyError(Exception):
    pass


class NotDeletedError(Exception):
    """Wiederherstellungsversuch für einen Ordner, der gar nicht im
    Papierkorb liegt (5.2, seit P7-S1b)."""


class RestorePeriodExpiredError(Exception):
    """Die konfigurierte Papierkorb-Wiederherstellungsfrist ist bereits
    abgelaufen (5.2, seit P7-S1b)."""


class AlreadyReleasedError(Exception):
    """Ein Legal Hold wurde bereits zuvor aufgehoben (5.2, seit P7-S1b)."""


async def ensure_root_folder(session: AsyncSession) -> None:
    existing = await session.get(Folder, ROOT_FOLDER_ID)
    if existing is None:
        now = datetime.now(UTC)
        session.add(
            Folder(
                id=ROOT_FOLDER_ID,
                name="Root",
                parent_id=None,
                object_type_id=None,
                attributes={},
                created_by="system",
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()


async def _get_folder_row(session: AsyncSession, folder_id: str) -> Folder:
    """Roher Zugriff ohne Papierkorb-Filter - für Aufbewahrungs-/Legal-Hold-
    Operationen (5.2, seit P7-S1b), die auch auf einem bereits im Papierkorb
    liegenden Ordner funktionieren müssen (`restore_folder` u. a.)."""
    folder = await session.get(Folder, folder_id)
    if folder is None:
        raise NotFoundError(f"folder_id {folder_id!r} unbekannt")
    return folder


async def get_folder(session: AsyncSession, folder_id: str) -> Folder:
    """Behandelt einen soft-gelöschten Ordner wie nicht existent (5.2, seit
    P7-S1b) - verhindert, dass neue Kinder unter einem im Papierkorb
    liegenden Ordner angelegt oder Ordner dorthin verschoben werden."""
    folder = await _get_folder_row(session, folder_id)
    if folder.deleted_at is not None:
        raise NotFoundError(f"folder_id {folder_id!r} unbekannt")
    return folder


async def get_folder_any_state(session: AsyncSession, folder_id: str) -> Folder:
    """Öffentliches Gegenstück zu `get_folder` OHNE Papierkorb-Filter (2.5,
    P15-S1) - für die manuelle Löschadministrations-Löschung, die gerade
    einen bereits im Papierkorb liegenden Ordner ansprechen muss. Reiner
    Namens-Wrapper um `_get_folder_row`, damit `main.py` nicht auf eine als
    privat markierte Funktion zugreift."""
    return await _get_folder_row(session, folder_id)


async def list_children(session: AsyncSession, folder_id: str) -> list[Folder]:
    await get_folder(session, folder_id)
    result = await session.execute(
        select(Folder)
        .where(Folder.parent_id == folder_id, Folder.deleted_at.is_(None))
        .order_by(Folder.name)
    )
    return list(result.scalars().all())


async def create_folder(
    session: AsyncSession,
    *,
    name: str,
    parent_id: str,
    object_type_id: int | None,
    attributes: dict,
    created_by: str,
) -> Folder:
    await get_folder(session, parent_id)  # 404, falls Elternordner unbekannt/gelöscht

    now = datetime.now(UTC)
    folder = Folder(
        id=str(uuid.uuid4()),
        name=name,
        parent_id=parent_id,
        object_type_id=object_type_id,
        attributes=attributes,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(folder)
    await session.flush()
    return folder


async def update_folder(
    session: AsyncSession,
    folder_id: str,
    *,
    name: str | None,
    new_parent_id: str | None,
    attributes: dict | None,
) -> tuple[Folder, bool]:
    """Aktualisiert Name/Attribute und optional den Elternordner (Verschieben).
    Gibt zurück, ob sich der Elternordner tatsächlich geändert hat, damit der
    Aufrufer nur dann ein ``.resource.moved``-Event publiziert."""
    folder = await get_folder(session, folder_id)
    moved = False

    if new_parent_id is not None and new_parent_id != folder.parent_id:
        if new_parent_id == folder_id:
            raise ValueError("Ein Ordner kann nicht sein eigener Elternordner sein")
        await get_folder(session, new_parent_id)
        folder.parent_id = new_parent_id
        moved = True

    if name is not None:
        folder.name = name
    if attributes is not None:
        folder.attributes = attributes

    folder.updated_at = datetime.now(UTC)
    await session.flush()
    return folder, moved


async def delete_folder(session: AsyncSession, folder_id: str) -> None:
    """Sofortiger Hard-Delete - bleibt als Fallback für bereits-leere,
    nie-retention-behaftete Fälle bestehen (siehe `soft_delete_folder` für
    den regulären Papierkorb-Weg, seit P7-S1b)."""
    folder = await get_folder(session, folder_id)
    children = await list_children(session, folder_id)
    if children:
        raise FolderNotEmptyError(f"Ordner {folder_id!r} enthält noch {len(children)} Unterordner")
    await session.delete(folder)
    await session.flush()


# --- Aufbewahrung/Legal Hold/Zwangslöschung für Ordner (5.2/5.2a, seit P7-S1b) ---


async def list_active_subtree_ids(session: AsyncSession, folder_id: str) -> list[str]:
    """Ordner-ID + alle aktiven (nicht bereits gelöschten) Nachfahren über
    beliebig viele Ebenen - Grundlage für die Papierkorb-Kaskade sowie dafür,
    welche Ordner-IDs bei der Nicht-leer-Prüfung vor einer Zwangslöschung auf
    aktive Dokumente hin abgefragt werden (siehe `document_client.count_active`)."""
    ids = [folder_id]
    frontier = [folder_id]
    while frontier:
        result = await session.execute(
            select(Folder.id).where(Folder.parent_id.in_(frontier), Folder.deleted_at.is_(None))
        )
        children = [row[0] for row in result.all()]
        ids.extend(children)
        frontier = children
    return ids


async def has_any_child_folder_row(session: AsyncSession, folder_id: str) -> bool:
    """Nicht-leer-Prüfung vor Zwangslöschung, Teil 2 (5.2a, seit P7-S1b) -
    anders als `list_active_subtree_ids` bewusst OHNE `deleted_at`-Filter:
    ein bereits soft-gelöschter (aber noch nicht per Papierkorb-Ablauf
    physisch bereinigter) Unterordner ist als DB-Zeile weiterhin vorhanden
    und referenziert diesen Ordner per FK (`parent_id`) - ein physisches
    Entfernen des Elternordners würde sonst mit einer `ForeignKeyViolation`
    fehlschlagen (live beim P7-S1b-Smoke-Test gefunden, siehe PROGRESS.md).
    Bewusst kein automatisches Mit-Bereinigen des bereits im Papierkorb
    liegenden Unterordners - der hat noch seine eigene, unabhängige
    Wiederherstellungsfrist."""
    result = await session.execute(select(Folder.id).where(Folder.parent_id == folder_id).limit(1))
    return result.scalar_one_or_none() is not None


async def soft_delete_folder(
    session: AsyncSession, folder_id: str, *, deleted_by: str, document_client: DocumentClient
) -> Folder:
    """Papierkorb-Weg (5.2, seit P7-S1b) - kaskadiert über den gesamten
    aktiven Teilbaum: Unterordner werden hier direkt mitsoft-gelöscht,
    enthaltene Dokumente über einen synchronen Aufruf an `document-service`
    (siehe `document_client.py`). Bereits unabhängig gelöschte Unterordner
    bleiben unangetastet - ihr `deleted_via_folder_id` würde sonst
    fälschlich überschrieben und bei einer künftigen Wiederherstellung
    dieses Ordners mit zurückgeholt, obwohl sie eigenständig gelöscht wurden."""
    folder = await get_folder(session, folder_id)
    subtree_ids = await list_active_subtree_ids(session, folder_id)
    now = datetime.now(UTC)
    for subtree_id in subtree_ids:
        node = await session.get(Folder, subtree_id)
        node.deleted_at = now
        node.deleted_by = deleted_by
        node.updated_at = now
        if subtree_id != folder_id:
            node.deleted_via_folder_id = folder_id
    await session.flush()
    await document_client.cascade_trash(subtree_ids, via_folder_id=folder_id, deleted_by=deleted_by)
    return folder


async def restore_folder(
    session: AsyncSession, folder_id: str, *, document_client: DocumentClient
) -> Folder:
    """Papierkorb-Wiederherstellung (5.2, seit P7-S1b) - stellt den Ordner
    selbst sowie alle über ihn kaskadiert gelöschten Unterordner/Dokumente
    wieder her, nur innerhalb der konfigurierten Frist."""
    folder = await _get_folder_row(session, folder_id)
    if folder.deleted_at is None:
        raise NotDeletedError(f"Ordner {folder_id!r} ist nicht gelöscht")
    config = await get_trash_config(session)
    deadline = folder.deleted_at + timedelta(days=config.restore_period_days)
    if datetime.now(UTC) > deadline:
        raise RestorePeriodExpiredError(
            f"Wiederherstellungsfrist ({config.restore_period_days} Tage) ist abgelaufen"
        )
    now = datetime.now(UTC)
    folder.deleted_at = None
    folder.deleted_by = None
    folder.deleted_via_folder_id = None
    folder.updated_at = now

    result = await session.execute(select(Folder).where(Folder.deleted_via_folder_id == folder_id))
    for cascaded in result.scalars().all():
        cascaded.deleted_at = None
        cascaded.deleted_by = None
        cascaded.deleted_via_folder_id = None
        cascaded.updated_at = now
    await session.flush()

    await document_client.cascade_restore(folder_id)
    return folder


async def list_deleted_folders(
    session: AsyncSession, *, parent_id: str | None = None, deleted_by: str | None = None
) -> list[Folder]:
    """Papierkorb-Inhalt (5.2, seit P7-S1b; um `deleted_by`-Filter erweitert
    seit P15-S1). Ohne `parent_id` liefert dies den installationsweiten
    Papierkorb (persönlicher Papierkorb/Löschadministrations-Ansicht, 2.5)
    statt nur den eines einzelnen Ordners."""
    query = select(Folder).where(Folder.deleted_at.isnot(None))
    if parent_id is not None:
        query = query.where(Folder.parent_id == parent_id)
    if deleted_by is not None:
        query = query.where(Folder.deleted_by == deleted_by)
    result = await session.execute(query.order_by(Folder.name))
    return list(result.scalars().all())


async def hard_delete_folder(session: AsyncSession, folder_id: str) -> None:
    """Vollständige, unwiederbringliche Entfernung (5.2a, seit P7-S1b) -
    entfernt zuerst die Legal-Hold-Historie, damit die FK-Constraint nicht
    verletzt wird (gleiches Zwischen-Flush-Muster wie
    `document_service.repository.hard_delete_document`)."""
    folder = await _get_folder_row(session, folder_id)
    for hold in await list_holds(session, folder_id):
        await session.delete(hold)
    await session.flush()
    await session.delete(folder)
    await session.flush()


async def set_retention(
    session: AsyncSession,
    folder_id: str,
    *,
    retention_until: datetime | None,
    full_deletion: bool,
    reason: str | None,
    notify_email: str | None = None,
) -> Folder:
    folder = await _get_folder_row(session, folder_id)
    folder.retention_until = retention_until
    folder.full_deletion = full_deletion
    folder.pending_deletion_reason = reason
    folder.reminder_notify_email = notify_email
    folder.deletion_reminder_sent_at = None
    folder.force_delete_approval_requested_at = None
    folder.updated_at = datetime.now(UTC)
    await session.flush()
    return folder


async def create_legal_hold(
    session: AsyncSession, folder_id: str, *, set_by: str, reason: str | None
) -> LegalHold:
    await _get_folder_row(session, folder_id)
    hold = LegalHold(
        id=str(uuid.uuid4()),
        folder_id=folder_id,
        reason=reason,
        set_by=set_by,
        set_at=datetime.now(UTC),
    )
    session.add(hold)
    await session.flush()
    return hold


async def release_legal_hold(session: AsyncSession, hold_id: str, *, released_by: str) -> LegalHold:
    hold = await session.get(LegalHold, hold_id)
    if hold is None:
        raise NotFoundError(f"Legal Hold {hold_id!r} unbekannt")
    if hold.released_at is not None:
        raise AlreadyReleasedError(f"Legal Hold {hold_id!r} wurde bereits aufgehoben")
    hold.released_by = released_by
    hold.released_at = datetime.now(UTC)
    await session.flush()
    return hold


async def list_holds(
    session: AsyncSession, folder_id: str, *, active_only: bool = False
) -> list[LegalHold]:
    query = select(LegalHold).where(LegalHold.folder_id == folder_id)
    if active_only:
        query = query.where(LegalHold.released_at.is_(None))
    result = await session.execute(query.order_by(LegalHold.set_at.desc()))
    return list(result.scalars().all())


async def has_active_hold(session: AsyncSession, folder_id: str) -> bool:
    result = await session.execute(
        select(LegalHold.id)
        .where(LegalHold.folder_id == folder_id, LegalHold.released_at.is_(None))
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def create_deletion_register_entry(
    session: AsyncSession,
    folder_id: str,
    *,
    trigger: str,
    reason: str | None,
    triggered_by: str | None,
) -> DeletionRegisterEntry:
    entry = DeletionRegisterEntry(
        id=str(uuid.uuid4()),
        folder_id=folder_id,
        trigger=trigger,
        reason=reason,
        triggered_by=triggered_by,
        occurred_at=datetime.now(UTC),
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_deletion_register(
    session: AsyncSession, *, folder_id: str | None = None
) -> list[DeletionRegisterEntry]:
    query = select(DeletionRegisterEntry)
    if folder_id is not None:
        query = query.where(DeletionRegisterEntry.folder_id == folder_id)
    result = await session.execute(query.order_by(DeletionRegisterEntry.occurred_at.desc()))
    return list(result.scalars().all())


async def get_retention_config(session: AsyncSession) -> RetentionConfig:
    config = await session.get(RetentionConfig, _RETENTION_CONFIG_ID)
    if config is None:
        config = RetentionConfig(
            id=_RETENTION_CONFIG_ID,
            deletion_reason_required=False,
            reminder_lead_days=None,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_retention_config(
    session: AsyncSession, *, deletion_reason_required: bool, reminder_lead_days: int | None
) -> RetentionConfig:
    config = await get_retention_config(session)
    config.deletion_reason_required = deletion_reason_required
    config.reminder_lead_days = reminder_lead_days
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def get_trash_config(session: AsyncSession) -> TrashConfig:
    config = await session.get(TrashConfig, _TRASH_CONFIG_ID)
    if config is None:
        config = TrashConfig(
            id=_TRASH_CONFIG_ID, restore_period_days=30, updated_at=datetime.now(UTC)
        )
        session.add(config)
        await session.flush()
    return config


async def update_trash_config(session: AsyncSession, *, restore_period_days: int) -> TrashConfig:
    config = await get_trash_config(session)
    config.restore_period_days = restore_period_days
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def list_due_for_reminder(session: AsyncSession, *, lead_days: int) -> list[Folder]:
    threshold = datetime.now(UTC) + timedelta(days=lead_days)
    result = await session.execute(
        select(Folder).where(
            Folder.retention_until.isnot(None),
            Folder.retention_until <= threshold,
            Folder.deleted_at.is_(None),
            Folder.deletion_reminder_sent_at.is_(None),
        )
    )
    candidates = list(result.scalars().all())
    return [f for f in candidates if not await has_active_hold(session, f.id)]


async def list_due_for_retention_action(session: AsyncSession) -> list[Folder]:
    now = datetime.now(UTC)
    result = await session.execute(
        select(Folder).where(
            Folder.retention_until.isnot(None),
            Folder.retention_until <= now,
            Folder.deleted_at.is_(None),
        )
    )
    candidates = list(result.scalars().all())
    return [f for f in candidates if not await has_active_hold(session, f.id)]


async def list_expired_trash(session: AsyncSession, *, restore_period_days: int) -> list[Folder]:
    deadline = datetime.now(UTC) - timedelta(days=restore_period_days)
    result = await session.execute(
        select(Folder).where(Folder.deleted_at.isnot(None), Folder.deleted_at <= deadline)
    )
    candidates = list(result.scalars().all())
    return [f for f in candidates if not await has_active_hold(session, f.id)]
