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
    """Restore attempt for a folder that is not actually in the trash
    (5.2, since P7-S1b)."""


class RestorePeriodExpiredError(Exception):
    """The configured trash restore period has already expired (5.2, since
    P7-S1b)."""


class AlreadyReleasedError(Exception):
    """A legal hold has already been released previously (5.2, since
    P7-S1b)."""


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
    """Inbox/Outbox (2.5/3.3, P15-S3) - identical idempotency pattern as
    `ensure_root_folder` (get-by-fixed-PK, insert-if-missing), attached
    directly under `root` instead of being root-level themselves, so the
    User UI's normal folder navigation can display them without a special
    case."""
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
    """Raw access without a trash filter - for retention/legal-hold
    operations (5.2, since P7-S1b) that must also work on a folder already
    in the trash (`restore_folder`, among others)."""
    folder = await session.get(Folder, folder_id)
    if folder is None:
        raise NotFoundError(f"folder_id {folder_id!r} unbekannt")
    return folder


async def get_folder(session: AsyncSession, folder_id: str) -> Folder:
    """Treats a soft-deleted folder as non-existent (5.2, since P7-S1b) -
    prevents new children from being created under a folder in the trash,
    or folders being moved there."""
    folder = await _get_folder_row(session, folder_id)
    if folder.deleted_at is not None:
        raise NotFoundError(f"folder_id {folder_id!r} unbekannt")
    return folder


async def get_folder_any_state(session: AsyncSession, folder_id: str) -> Folder:
    """Public counterpart to `get_folder` WITHOUT a trash filter (2.5,
    P15-S1) - for manual deletion-administration deletion, which needs to
    address exactly a folder already in the trash. Pure naming wrapper
    around `_get_folder_row`, so `main.py` does not access a function
    marked as private."""
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
    await get_folder(session, parent_id)  # 404 if parent folder is unknown/deleted

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
    """Updates name/attributes and optionally the parent folder (move).
    Returns whether the parent folder actually changed, so the caller only
    publishes a ``.resource.moved`` event in that case."""
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
    """Immediate hard delete - remains as a fallback for already-empty
    cases that never had retention applied (see `soft_delete_folder` for
    the regular trash path, since P7-S1b)."""
    folder = await get_folder(session, folder_id)
    children = await list_children(session, folder_id)
    if children:
        raise FolderNotEmptyError(f"Ordner {folder_id!r} enthält noch {len(children)} Unterordner")
    await session.delete(folder)
    await session.flush()


# --- Retention/legal hold/forced deletion for folders (5.2/5.2a, since P7-S1b) ---


async def list_active_subtree_ids(session: AsyncSession, folder_id: str) -> list[str]:
    """Folder ID + all active (not already deleted) descendants across any
    number of levels - basis for the trash cascade, as well as for which
    folder IDs are queried for active documents during the not-empty check
    before a forced deletion (see `document_client.count_active`)."""
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
    """Not-empty check before forced deletion, part 2 (5.2a, since P7-S1b) -
    unlike `list_active_subtree_ids`, deliberately WITHOUT a `deleted_at`
    filter: a subfolder that is already soft-deleted (but not yet
    physically cleaned up via trash expiry) is still present as a DB row
    and references this folder via FK (`parent_id`) - physically removing
    the parent folder would otherwise fail with a `ForeignKeyViolation`
    (found live during the P7-S1b smoke test, see PROGRESS.md).
    Deliberately no automatic cascading cleanup of a subfolder already in
    the trash - it still has its own, independent restore period."""
    result = await session.execute(select(Folder.id).where(Folder.parent_id == folder_id).limit(1))
    return result.scalar_one_or_none() is not None


async def soft_delete_folder(
    session: AsyncSession, folder_id: str, *, deleted_by: str, document_client: DocumentClient
) -> Folder:
    """Trash path (5.2, since P7-S1b) - cascades over the entire active
    subtree: subfolders are soft-deleted directly here, contained documents
    via a synchronous call to `document-service` (see `document_client.py`).
    Subfolders already independently deleted remain untouched - otherwise
    their `deleted_via_folder_id` would be incorrectly overwritten and they
    would be retrieved again on a future restore of this folder, even
    though they were deleted independently."""
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
    """Trash restore (5.2, since P7-S1b) - restores the folder itself as
    well as all subfolders/documents that were deleted via cascade through
    it, only within the configured retention period."""
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
    """Trash contents (5.2, since P7-S1b; extended with a `deleted_by`
    filter since P15-S1). Without `parent_id`, this returns the
    installation-wide trash (personal trash/deletion-administration view,
    2.5) instead of only that of a single folder."""
    query = select(Folder).where(Folder.deleted_at.isnot(None))
    if parent_id is not None:
        query = query.where(Folder.parent_id == parent_id)
    if deleted_by is not None:
        query = query.where(Folder.deleted_by == deleted_by)
    result = await session.execute(query.order_by(Folder.name))
    return list(result.scalars().all())


async def hard_delete_folder(session: AsyncSession, folder_id: str) -> None:
    """Complete, irrecoverable removal (5.2a, since P7-S1b) - first removes
    the legal-hold history so that the FK constraint is not violated (same
    interim-flush pattern as
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


# --- Structure templates (2.5/7.3, since P15-S6) ---


async def _build_structure_node(session: AsyncSession, folder_id: str) -> dict:
    folder = await get_folder(session, folder_id)
    children = await list_children(session, folder_id)
    return {
        "name": folder.name,
        "object_type_id": folder.object_type_id,
        "children": [await _build_structure_node(session, child.id) for child in children],
    }


async def build_template_structure(session: AsyncSession, folder_id: str) -> dict:
    """Captures the active subtree starting at `folder_id` as a nested
    structure tree (2.5/7.3, P15-S6) - only name/`object_type_id` per node,
    deliberately no attribute values (skeleton, see ADR 0056).
    `list_children` already excludes soft-deleted subfolders."""
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
    """Applies a structure template below `target_parent_id` (2.5/7.3,
    P15-S6) - creates a real folder for each node via the regular
    `create_folder` base function, deliberately WITHOUT the object-type
    validation from `main.py`'s `_validate_against_object_type` (skeleton:
    required attributes are naturally not yet populated at application
    time, they are only checked later when filled in via PATCH - see
    ADR 0056). Returns all newly created folders, root of the applied
    subtree first."""
    await get_folder(session, target_parent_id)  # 404 if target is unknown/deleted
    return await _apply_structure_node(
        session, template.structure, parent_id=target_parent_id, created_by=created_by
    )
