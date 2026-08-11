import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from folder_service.document_client import DocumentClient
from folder_service.models import (
    DeletionRegisterEntry,
    Folder,
    FolderTemplate,
    LegalHold,
    RetentionConfig,
    TrashConfig,
)
from folder_service.settings import INBOX_FOLDER_ID, OUTBOX_FOLDER_ID, ROOT_FOLDER_ID

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


async def ensure_special_folders(session: AsyncSession) -> None:
    """Posteingang/Postausgang (2.5/3.3, P15-S3) - identisches Idempotenz-
    Muster wie `ensure_root_folder` (get-by-fester-PK, insert-if-missing),
    direkt unter `root` gehängt statt selbst wurzelständig, damit die
    normale Ordner-Navigation der User-UI sie ohne Sonderfall anzeigen
    kann."""
    now = datetime.now(UTC)
    for folder_id, name in ((INBOX_FOLDER_ID, "Posteingang"), (OUTBOX_FOLDER_ID, "Postausgang")):
        existing = await session.get(Folder, folder_id)
        if existing is None:
            session.add(
                Folder(
                    id=folder_id,
                    name=name,
                    parent_id=ROOT_FOLDER_ID,
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


# --- Struktur-Vorlagen (2.5/7.3, seit P15-S6) ---


async def _build_structure_node(session: AsyncSession, folder_id: str) -> dict:
    folder = await get_folder(session, folder_id)
    children = await list_children(session, folder_id)
    return {
        "name": folder.name,
        "object_type_id": folder.object_type_id,
        "children": [await _build_structure_node(session, child.id) for child in children],
    }


async def build_template_structure(session: AsyncSession, folder_id: str) -> dict:
    """Erfasst den aktiven Teilbaum ab `folder_id` als verschachtelten
    Struktur-Baum (2.5/7.3, P15-S6) - nur Name/`object_type_id` je Knoten,
    bewusst keine Attributwerte (Rohbau, siehe ADR 0056). `list_children`
    schließt bereits soft-gelöschte Unterordner aus."""
    return await _build_structure_node(session, folder_id)


async def create_template(
    session: AsyncSession, *, name: str, description: str | None, structure: dict, created_by: str
) -> FolderTemplate:
    template = FolderTemplate(
        id=str(uuid.uuid4()),
        name=name,
        description=description,
        structure=structure,
        created_by=created_by,
        created_at=datetime.now(UTC),
    )
    session.add(template)
    await session.flush()
    return template


async def list_templates(session: AsyncSession) -> list[FolderTemplate]:
    result = await session.execute(select(FolderTemplate).order_by(FolderTemplate.name))
    return list(result.scalars().all())


async def get_template(session: AsyncSession, template_id: str) -> FolderTemplate:
    template = await session.get(FolderTemplate, template_id)
    if template is None:
        raise NotFoundError(f"folder_template_id {template_id!r} unbekannt")
    return template


async def delete_template(session: AsyncSession, template_id: str) -> None:
    template = await get_template(session, template_id)
    await session.delete(template)
    await session.flush()


async def _apply_structure_node(
    session: AsyncSession, node: dict, *, parent_id: str, created_by: str
) -> list[Folder]:
    folder = await create_folder(
        session,
        name=node["name"],
        parent_id=parent_id,
        object_type_id=node.get("object_type_id"),
        attributes={},
        created_by=created_by,
    )
    created = [folder]
    for child in node.get("children", []):
        created.extend(
            await _apply_structure_node(session, child, parent_id=folder.id, created_by=created_by)
        )
    return created


async def apply_template(
    session: AsyncSession, template: FolderTemplate, *, target_parent_id: str, created_by: str
) -> list[Folder]:
    """Wendet eine Struktur-Vorlage unterhalb `target_parent_id` an (2.5/7.3,
    P15-S6) - erzeugt für jeden Knoten einen echten Ordner über die reguläre
    `create_folder`-Grundfunktion, bewusst OHNE die object-type-Validierung
    aus `main.py`s `_validate_against_object_type` (Rohbau: Pflichtattribute
    sind zum Anwendungszeitpunkt naturgemäß noch nicht befüllt, werden erst
    beim späteren Ausfüllen via PATCH geprüft - siehe ADR 0056). Gibt alle
    neu erzeugten Ordner zurück, Wurzel des angewendeten Teilbaums zuerst."""
    await get_folder(session, target_parent_id)  # 404, falls Ziel unbekannt/gelöscht
    return await _apply_structure_node(
        session, template.structure, parent_id=target_parent_id, created_by=created_by
    )
