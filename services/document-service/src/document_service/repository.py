import secrets
import uuid
from datetime import UTC, datetime, timedelta

from dms_retry import compute_backoff_seconds
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from document_service.models import (
    AuditTraceConfig,
    AuditTraceRoleOverride,
    DeletionRegisterEntry,
    Document,
    DocumentLock,
    DocumentVersion,
    ExportConfig,
    FolderExportJob,
    LegalHold,
    RetentionConfig,
    ShareLink,
    ShareLinkConfig,
    TrashConfig,
    UploadConfig,
    WebdavEditToken,
)

_UPLOAD_CONFIG_ID = 1
_RETENTION_CONFIG_ID = 1
_TRASH_CONFIG_ID = 1
_AUDIT_TRACE_CONFIG_ID = 1
_SHARE_LINK_CONFIG_ID = 1
_EXPORT_CONFIG_ID = 1


class NotFoundError(Exception):
    pass


class LockConflictError(Exception):
    """Another user currently holds the editing lock (4.2)."""


class LockNotHeldError(Exception):
    """A regular unlock attempt by someone who does not hold the lock."""


class NotDeletedError(Exception):
    """Restore attempt for a document that is not in the
    trash at all (5.2, since P7-S1)."""


class RestorePeriodExpiredError(Exception):
    """The configured trash restore period has already
    expired (5.2, since P7-S1)."""


class AlreadyReleasedError(Exception):
    """A legal hold was already released previously (5.2, since P7-S1)."""


class AlreadyRegisteredError(Exception):
    """Register attempt for a document that already has a `registered_at`
    (post-roadmap phase 31 session 2, ADR 0113) - registration is a one-way
    transition, there is no "un-register"."""


class ClassificationDowngradeError(Exception):
    """A classification-level change attempt that would lower the rank
    (post-roadmap phase 31 session 3, ADR 0114) - `PUT
    .../classification-level` only ever sets or raises, see
    `set_classification_level` below."""


# Rank order for the four levels from `ClassificationLevelUpdate`
# (post-roadmap phase 31 session 3, ADR 0114) - `None` (unclassified) is
# implicitly rank 0, lower than every named level.
CLASSIFICATION_RANK = {
    "VS-NfD": 1,
    "VS-VERTRAULICH": 2,
    "GEHEIM": 3,
    "STRENG GEHEIM": 4,
}


async def get_document(session: AsyncSession, document_id: str) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"document_id {document_id!r} unbekannt")
    return document


async def list_documents_by_folder(session: AsyncSession, folder_id: str) -> list[Document]:
    """Basis for the folder navigation of the user UI (P4-S2). `folder_id` is
    treated here, as everywhere else in this service, as an opaque foreign
    reference (no existence check against the Folder Service) - an unknown
    folder simply returns an empty list instead of an error."""
    result = await session.execute(
        select(Document)
        .where(Document.folder_id == folder_id, Document.deleted_at.is_(None))
        .order_by(Document.title)
    )
    return list(result.scalars().all())


async def list_documents_by_kennzeichen(session: AsyncSession, kennzeichen: str) -> list[Document]:
    """Cross-object-type reference number search (2.5/3.3, P15-S3) - for the
    new `mail-connector`, which wants to match incoming mail to an existing
    document based on a reference number found in the subject/body.
    Deliberately returns a list instead of a single object: a `Kennzeichen`
    (reference number) is only unique per object type + year (P5e-S1 counter
    scheme), not globally - two different object types with an identical
    format can produce the same rendered string. The caller must check
    uniqueness itself (0/1/N matches)."""
    result = await session.execute(
        select(Document).where(
            Document.attributes["Kennzeichen"].as_string() == kennzeichen,
            Document.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def list_derived_documents(session: AsyncSession, document_id: str) -> list[Document]:
    """First actual reader of `derived_from_document_id` (post-roadmap phase
    31 session 4, ADR 0115) - the P6-S3 provenance fields were write-only
    until this session (round-tripped in `DocumentOut`, but never queried by
    anything). Used by the redaction workflow to show an original document's
    redacted copies, but deliberately not redaction-specific itself (no
    `derivation_type` filter) - any future derivation reason benefits from
    the same "what was derived from me" lookup."""
    result = await session.execute(
        select(Document)
        .where(Document.derived_from_document_id == document_id, Document.deleted_at.is_(None))
        .order_by(Document.created_at)
    )
    return list(result.scalars().all())


async def create_document(
    session: AsyncSession,
    *,
    document_id: str,
    title: str,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    checksum_sha256: str,
    storage_object_key: str,
    folder_id: str | None,
    object_type_id: int | None,
    attributes: dict,
    created_by: str,
    derived_from_document_id: str | None = None,
    derived_from_version_number: int | None = None,
    originating_case_id: str | None = None,
    retention_until: datetime | None = None,
    archive_after: datetime | None = None,
    draft: bool = False,
    classification_level: str | None = None,
    derivation_type: str | None = None,
) -> Document:
    now = datetime.now(UTC)
    document = Document(
        id=document_id,
        title=title,
        folder_id=folder_id,
        object_type_id=object_type_id,
        attributes=attributes,
        current_version_number=1,
        created_by=created_by,
        created_at=now,
        updated_at=now,
        derived_from_document_id=derived_from_document_id,
        derived_from_version_number=derived_from_version_number,
        originating_case_id=originating_case_id,
        # Typed cross-reference (post-roadmap phase 31 session 4, ADR 0115).
        derivation_type=derivation_type,
        # Retention (5.2, since P7-S1): copied once from
        # `ObjectType.default_retention_days` if no manual date was set at
        # creation time (see main.py create_document) - later changes to the
        # type default do not apply retroactively.
        retention_until=retention_until,
        # Records disposal (5.6, since P7-S3): copied analogously from
        # `ObjectType.default_archive_after_days`, independent of
        # retention_until (see models.py Document.archive_after).
        archive_after=archive_after,
        # Draft / pre-registration lifecycle (post-roadmap phase 31 session
        # 2, ADR 0113): a regular document is "registered" the moment it is
        # created (no `Kennzeichen` was withheld), a draft stays `None`
        # until `register_document` below is called explicitly.
        registered_at=None if draft else now,
        # Classification level (post-roadmap phase 31 session 3, ADR 0114):
        # copied once from `ObjectType.classification_level` (see main.py) -
        # a seed/default, not an ongoing constraint, same pattern as
        # `retention_until`/`archive_after` above.
        classification_level=classification_level,
    )
    session.add(document)
    session.add(
        DocumentVersion(
            document_id=document_id,
            version_number=1,
            storage_object_key=storage_object_key,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
            is_conflict=False,
            based_on_version_number=None,
            # Version-1's snapshot equals the document's initial value -
            # there is no earlier state to diverge from yet.
            classification_level=classification_level,
            created_by=created_by,
            created_at=now,
        )
    )
    await session.flush()
    return document


async def update_document_metadata(
    session: AsyncSession,
    document_id: str,
    *,
    title: str | None,
    attributes: dict | None,
    folder_id: str | None = None,
) -> Document:
    """Metadata update (P4-S4, user feedback: attributes were previously only
    settable at creation time). Deliberately does not change `object_type_id`
    - that would be a retyping with its own consistency concerns, not a pure
    metadata update. `folder_id` (moving, P12-S1), by contrast, has been
    first-class since the WebDAV connector user request - existence/
    placement constraint checking already happens in the caller (`main.py`),
    here it is just the plain assignment."""
    document = await get_document(session, document_id)
    if title is not None:
        document.title = title
    if attributes is not None:
        document.attributes = attributes
    if folder_id is not None:
        document.folder_id = folder_id
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


async def register_document(
    session: AsyncSession, document_id: str, *, kennzeichen: str | None
) -> Document:
    """Draft -> registered transition (post-roadmap phase 31 session 2, ADR
    0113) - the counterpart to the at-creation-time assignment in
    `create_document` above, for a document created with `draft=True`.
    `kennzeichen` is resolved by the caller (`main.py`, via
    `ObjectTypeClient.next_kennzeichen`) since that is an HTTP call to
    Object-Type Service, not a repository concern; `None` here simply means
    no generator is configured for the document's object type, exactly like
    at creation time. One-way: once registered, a document cannot be
    un-registered again."""
    document = await get_document(session, document_id)
    if document.registered_at is not None:
        raise AlreadyRegisteredError(f"document_id {document_id!r} ist bereits registriert")
    if kennzeichen is not None:
        document.attributes = {**document.attributes, "Kennzeichen": kennzeichen}
    document.registered_at = datetime.now(UTC)
    document.updated_at = document.registered_at
    await session.flush()
    return document


async def set_classification_level(
    session: AsyncSession, document_id: str, *, classification_level: str
) -> Document:
    """Set or raise a document's classification level (post-roadmap phase
    31 session 3, ADR 0114). `classification_level` is always one of the
    four named levels (never `None` - a request to CLEAR/downgrade to
    unclassified is not offered by this action at all, see ADR 0114).
    Setting the same level again is treated as a no-op (idempotent, not an
    error) - only a strictly lower rank is rejected."""
    document = await get_document(session, document_id)
    current_rank = CLASSIFICATION_RANK.get(document.classification_level or "", 0)
    new_rank = CLASSIFICATION_RANK[classification_level]
    if new_rank < current_rank:
        raise ClassificationDowngradeError(
            f"Einstufung kann nur angehoben werden ({document.classification_level!r} -> "
            f"{classification_level!r} ist eine Herabstufung)"
        )
    document.classification_level = classification_level
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


async def delete_document(session: AsyncSession, document_id: str, *, deleted_by: str) -> Document:
    """Soft delete (visibility off, metadata remains) - triggered manually via
    the API. Since P7-S1, a soft-deleted document moves into the
    trash (`restore_document`/`list_deleted_documents`) and is
    automatically physically purged once `TrashConfig.restore_period_days`
    has elapsed (see `list_expired_trash`/main.py `_retention_poll_loop`).
    `deleted_by` was previously accepted but never persisted (a real gap
    found in P15-S0) - now a prerequisite for the personal
    trash (2.5, P15-S1)."""
    document = await get_document(session, document_id)
    document.deleted_at = datetime.now(UTC)
    document.deleted_by = deleted_by
    document.updated_at = document.deleted_at
    await session.flush()
    return document


async def restore_document(session: AsyncSession, document_id: str) -> Document:
    """Trash restore (5.2, since P7-S1) - only possible within the
    configured period (`TrashConfig.restore_period_days`)."""
    document = await get_document(session, document_id)
    if document.deleted_at is None:
        raise NotDeletedError(f"Dokument {document_id!r} ist nicht gelöscht")
    config = await get_trash_config(session)
    deadline = document.deleted_at + timedelta(days=config.restore_period_days)
    if datetime.now(UTC) > deadline:
        raise RestorePeriodExpiredError(
            f"Wiederherstellungsfrist ({config.restore_period_days} Tage) ist abgelaufen"
        )
    document.deleted_at = None
    document.deleted_by = None
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


async def cascade_trash_by_folder_ids(
    session: AsyncSession, folder_ids: list[str], *, via_folder_id: str, deleted_by: str
) -> list[str]:
    """Cascaded trash path for an entire folder subtree (5.2, since
    P7-S1b) - called synchronously by the `folder-service` when a folder
    (including subfolders) is moved to the trash. `deleted_via_
    folder_id` marks the origin so that `cascade_restore_by_via_folder_id`
    only retrieves these documents on restore, not ones independently
    deleted individually in the same folder."""
    if not folder_ids:
        return []
    result = await session.execute(
        select(Document).where(Document.folder_id.in_(folder_ids), Document.deleted_at.is_(None))
    )
    documents = list(result.scalars().all())
    now = datetime.now(UTC)
    for document in documents:
        document.deleted_at = now
        document.deleted_by = deleted_by
        document.deleted_via_folder_id = via_folder_id
        document.updated_at = now
    await session.flush()
    return [document.id for document in documents]


async def cascade_restore_by_via_folder_id(session: AsyncSession, via_folder_id: str) -> list[str]:
    """Counterpart to `cascade_trash_by_folder_ids` - only restores documents
    that were cascade-deleted via exactly this folder."""
    result = await session.execute(
        select(Document).where(Document.deleted_via_folder_id == via_folder_id)
    )
    documents = list(result.scalars().all())
    now = datetime.now(UTC)
    for document in documents:
        document.deleted_at = None
        document.deleted_by = None
        document.deleted_via_folder_id = None
        document.updated_at = now
    await session.flush()
    return [document.id for document in documents]


async def count_active_by_folder_ids(session: AsyncSession, folder_ids: list[str]) -> int:
    """Non-empty check before forced folder deletion (5.2a, since P7-S1b) -
    the `folder-service` uses this to query whether there are still active
    (non-deleted) documents in the subtree to be deleted."""
    if not folder_ids:
        return 0
    result = await session.execute(
        select(func.count())
        .select_from(Document)
        .where(Document.folder_id.in_(folder_ids), Document.deleted_at.is_(None))
    )
    return result.scalar_one()


async def count_active_total(session: AsyncSession) -> int:
    """Installation-wide document count (9.1, since P9-S1) - for the
    `license-service`'s usage check. Deliberately a separate function
    instead of misusing `count_active_by_folder_ids([])`: there, the empty
    list has the safety meaning "no folders checked, hence 0", not
    "no filter, hence all" - mixing both semantics in one function
    would be error-prone for the existing caller
    (forced folder deletion check, P7-S1b)."""
    result = await session.execute(
        select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))
    )
    return result.scalar_one()


async def list_deleted_documents(
    session: AsyncSession,
    *,
    folder_id: str | None = None,
    deleted_by: str | None = None,
    classified: bool | None = None,
) -> list[Document]:
    """Trash contents (5.2, since P7-S1; extended with `deleted_by`/
    classification filters since P15-S1, see main.py `list_deleted_documents`
    for the visibility rules that assemble these filters). Without
    `folder_id`, this returns the installation-wide trash (personal
    trash/deletion administration views, 2.5) instead of just that of a
    single folder - counterpart to `list_documents_by_folder`.

    `classified` (post-roadmap phase 31 session 3, ADR 0114): filters
    directly on the document's OWN `classification_level` - `True` = the
    classified-documents trash (`IS NOT NULL`), `False` = the regular trash
    (`IS NULL`), `None` = no filter. Before this session this was instead an
    indirect `object_type_id IN/NOT IN (...)` lookup requiring a live HTTP
    round trip to object-type-service on every call (see `main.py`'s
    `list_deleted_documents`) - now a plain column check, since
    classification is no longer purely an object-type-level property."""
    query = select(Document).where(Document.deleted_at.isnot(None))
    if folder_id is not None:
        query = query.where(Document.folder_id == folder_id)
    if deleted_by is not None:
        query = query.where(Document.deleted_by == deleted_by)
    if classified is True:
        query = query.where(Document.classification_level.isnot(None))
    elif classified is False:
        query = query.where(Document.classification_level.is_(None))
    result = await session.execute(query.order_by(Document.title))
    return list(result.scalars().all())


async def hard_delete_document(session: AsyncSession, document_id: str) -> None:
    """Complete, irrecoverable removal (5.2a, since P7-S1) - unlike
    `delete_document` (soft delete), nothing remains in this schema
    afterwards except a separate `DeletionRegisterEntry`
    (see main.py._execute_forced_deletion/_purge_expired_trash), which
    deliberately has NO FK to `Document.id`. First removes all
    dependent rows (versions, a possibly orphaned lock, the
    legal hold history) so that FK constraints are not violated."""
    document = await get_document(session, document_id)
    for version in await list_versions(session, document_id):
        await session.delete(version)
    lock = await session.get(DocumentLock, document_id)
    if lock is not None:
        await session.delete(lock)
    for hold in await list_holds(session, document_id):
        await session.delete(hold)
    # Without an explicit intermediate flush, SQLAlchemy's unit of work does
    # not reliably order the subsequent DELETE statement for `document` AFTER
    # the ones above (no declared `relationship()`s between these
    # models, only raw FK columns) - Postgres would otherwise reject
    # deletion of the parent object with an FK violation.
    await session.flush()
    await session.delete(document)
    await session.flush()


async def list_versions(session: AsyncSession, document_id: str) -> list[DocumentVersion]:
    await get_document(session, document_id)
    result = await session.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number)
    )
    return list(result.scalars().all())


async def get_version(
    session: AsyncSession, document_id: str, version_number: int
) -> DocumentVersion:
    result = await session.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise NotFoundError(f"Version {version_number} von {document_id!r} unbekannt")
    return version


async def get_current_version(session: AsyncSession, document_id: str) -> DocumentVersion:
    document = await get_document(session, document_id)
    return await get_version(session, document_id, document.current_version_number)


async def get_lock(session: AsyncSession, document_id: str) -> DocumentLock | None:
    return await session.get(DocumentLock, document_id)


def _is_active(lock: DocumentLock, now: datetime) -> bool:
    return lock.expires_at > now


async def acquire_lock(
    session: AsyncSession,
    document_id: str,
    *,
    locked_by: str,
    session_id: str,
    timeout_seconds: float,
) -> DocumentLock:
    document = await get_document(session, document_id)
    now = datetime.now(UTC)
    lock = await session.get(DocumentLock, document_id)

    if lock is not None and _is_active(lock, now) and lock.locked_by != locked_by:
        raise LockConflictError(f"Dokument {document_id!r} ist gesperrt von {lock.locked_by!r}")

    if lock is None:
        lock = DocumentLock(document_id=document_id)
        session.add(lock)

    lock.locked_by = locked_by
    lock.session_id = session_id
    lock.based_on_version_number = document.current_version_number
    lock.locked_at = now
    lock.expires_at = now + timedelta(seconds=timeout_seconds)
    # A fresh acquisition and a renewal by the same holder both restart the
    # "how long has this been locked" clock, so any previously sent
    # reminder is no longer relevant to the new locked_at (post-roadmap
    # phase 30 session 4, ADR 0111).
    lock.reminder_sent_at = None
    await session.flush()
    return lock


async def release_lock(session: AsyncSession, document_id: str, *, released_by: str) -> None:
    lock = await session.get(DocumentLock, document_id)
    if lock is None:
        return  # already free - idempotent, not an error
    if lock.locked_by != released_by:
        raise LockNotHeldError(
            f"Sperre an {document_id!r} wird von {lock.locked_by!r} gehalten, "
            f"nicht von {released_by!r}"
        )
    await session.delete(lock)
    await session.flush()


async def force_release_lock(session: AsyncSession, document_id: str) -> DocumentLock:
    """Administrative force unlock (4.2). Returns the previously active lock
    so the caller knows the original holder for notification/
    audit purposes. The actual conflict-copy safeguard does not happen here,
    but optimistically at the next check-in (see checkin_version) -
    see ADR 0002 for the rationale behind this simplification."""
    lock = await session.get(DocumentLock, document_id)
    if lock is None:
        raise NotFoundError(f"Dokument {document_id!r} ist nicht gesperrt")
    await session.delete(lock)
    await session.flush()
    return lock


def _conflict_filename(filename: str, *, created_by: str, now: datetime) -> str:
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    stem, sep, ext = filename.rpartition(".")
    if sep:
        return f"{stem}_conflict_{created_by}_{timestamp}.{ext}"
    return f"{filename}_conflict_{created_by}_{timestamp}"


async def checkin_version(
    session: AsyncSession,
    document_id: str,
    *,
    expected_base_version_number: int,
    storage_object_key: str,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    checksum_sha256: str,
    created_by: str,
    comment: str | None = None,
) -> tuple[DocumentVersion, bool]:
    """Optimistic conflict detection (4.2, elaborated): if the base version
    specified by the client does not match the current main version
    (e.g. because a force unlock + regular check-in by another user
    happened in the meantime), the upload is NOT applied as an
    overwrite, but stored as a standalone, still
    retrievable conflict copy alongside the current version - the
    main version pointer does not move in this case.

    Returns ``(version, is_conflict)``.
    """
    document = await get_document(session, document_id)
    lock = await session.get(DocumentLock, document_id)
    now = datetime.now(UTC)

    if lock is not None and _is_active(lock, now) and lock.locked_by != created_by:
        raise LockConflictError(f"Dokument {document_id!r} ist gesperrt von {lock.locked_by!r}")

    is_conflict = expected_base_version_number != document.current_version_number

    max_version = await session.execute(
        select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.document_id == document_id
        )
    )
    next_version_number = (max_version.scalar_one() or 0) + 1

    final_filename = (
        _conflict_filename(filename, created_by=created_by, now=now) if is_conflict else filename
    )

    version = DocumentVersion(
        document_id=document_id,
        version_number=next_version_number,
        storage_object_key=storage_object_key,
        filename=final_filename,
        content_type=content_type,
        size_bytes=size_bytes,
        checksum_sha256=checksum_sha256,
        is_conflict=is_conflict,
        based_on_version_number=expected_base_version_number,
        comment=comment,
        # Classification-level snapshot (post-roadmap phase 31 session 3,
        # ADR 0114): the document's CURRENT level at check-in time, not its
        # value at document-creation time - reflects any raise applied since
        # then, but (like every other version row) is never retroactively
        # rewritten by a later raise.
        classification_level=document.classification_level,
        created_by=created_by,
        created_at=now,
    )
    session.add(version)

    if not is_conflict:
        document.current_version_number = next_version_number
        document.updated_at = now

    # Check-in regularly ends one's own editing session (4.2) - even in the
    # conflict case, since the base version was outdated anyway and a
    # renewed attempt would also go through conflict detection.
    if lock is not None and lock.locked_by == created_by:
        await session.delete(lock)

    await session.flush()
    return version, is_conflict


async def get_upload_config(session: AsyncSession) -> UploadConfig:
    """Reads the (single) format whitelist row, creating it with defaults
    if it was never saved before - makes a separate migration/
    seed script unnecessary (same pattern as `ocr_service.get_config`)."""
    config = await session.get(UploadConfig, _UPLOAD_CONFIG_ID)
    if config is None:
        config = UploadConfig(
            id=_UPLOAD_CONFIG_ID, allowed_content_types=[], updated_at=datetime.now(UTC)
        )
        session.add(config)
        await session.flush()
    return config


async def update_upload_config(
    session: AsyncSession, *, allowed_content_types: list[str]
) -> UploadConfig:
    config = await get_upload_config(session)
    config.allowed_content_types = allowed_content_types
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


# --- Retention/Legal Hold/Forced Deletion (5.2/5.2a, since P7-S1) ---------


async def set_retention(
    session: AsyncSession,
    document_id: str,
    *,
    retention_until: datetime | None,
    full_deletion: bool,
    reason: str | None,
    notify_email: str | None = None,
) -> Document:
    document = await get_document(session, document_id)
    document.retention_until = retention_until
    document.full_deletion = full_deletion
    document.pending_deletion_reason = reason
    document.reminder_notify_email = notify_email
    # Newly scheduled (or date changed) - a reminder already sent for the
    # old date is moot, a new one can become due again. A previously
    # created approval request for the old date also no longer applies
    # automatically.
    document.deletion_reminder_sent_at = None
    document.force_delete_approval_requested_at = None
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


async def create_legal_hold(
    session: AsyncSession, document_id: str, *, set_by: str, reason: str | None
) -> LegalHold:
    await get_document(session, document_id)
    hold = LegalHold(
        id=str(uuid.uuid4()),
        document_id=document_id,
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
    session: AsyncSession, document_id: str, *, active_only: bool = False
) -> list[LegalHold]:
    query = select(LegalHold).where(LegalHold.document_id == document_id)
    if active_only:
        query = query.where(LegalHold.released_at.is_(None))
    result = await session.execute(query.order_by(LegalHold.set_at.desc()))
    return list(result.scalars().all())


async def has_active_hold(session: AsyncSession, document_id: str) -> bool:
    result = await session.execute(
        select(LegalHold.id)
        .where(LegalHold.document_id == document_id, LegalHold.released_at.is_(None))
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def create_deletion_register_entry(
    session: AsyncSession,
    document_id: str,
    *,
    trigger: str,
    reason: str | None,
    triggered_by: str | None,
) -> DeletionRegisterEntry:
    entry = DeletionRegisterEntry(
        id=str(uuid.uuid4()),
        document_id=document_id,
        trigger=trigger,
        reason=reason,
        triggered_by=triggered_by,
        occurred_at=datetime.now(UTC),
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_deletion_register(
    session: AsyncSession, *, document_id: str | None = None
) -> list[DeletionRegisterEntry]:
    query = select(DeletionRegisterEntry)
    if document_id is not None:
        query = query.where(DeletionRegisterEntry.document_id == document_id)
    result = await session.execute(query.order_by(DeletionRegisterEntry.occurred_at.desc()))
    return list(result.scalars().all())


async def get_retention_config(session: AsyncSession) -> RetentionConfig:
    config = await session.get(RetentionConfig, _RETENTION_CONFIG_ID)
    if config is None:
        config = RetentionConfig(
            id=_RETENTION_CONFIG_ID,
            deletion_reason_required=False,
            reminder_lead_days=None,
            deletion_reason_catalog=[],
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_retention_config(
    session: AsyncSession,
    *,
    deletion_reason_required: bool,
    reminder_lead_days: int | None,
    deletion_reason_catalog: list[str],
) -> RetentionConfig:
    config = await get_retention_config(session)
    config.deletion_reason_required = deletion_reason_required
    config.reminder_lead_days = reminder_lead_days
    config.deletion_reason_catalog = deletion_reason_catalog
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


async def list_due_for_reminder(session: AsyncSession, *, lead_days: int) -> list[Document]:
    """Deletion reminder (5.2a, optional): documents whose deadline falls
    within the lead time, are not yet deleted, have not yet received a
    reminder, and have no active legal hold."""
    threshold = datetime.now(UTC) + timedelta(days=lead_days)
    result = await session.execute(
        select(Document).where(
            Document.retention_until.isnot(None),
            Document.retention_until <= threshold,
            Document.deleted_at.is_(None),
            Document.deletion_reminder_sent_at.is_(None),
        )
    )
    candidates = list(result.scalars().all())
    return [d for d in candidates if not await has_active_hold(session, d.id)]


async def list_locks_due_for_reminder(
    session: AsyncSession, *, threshold_seconds: float
) -> list[DocumentLock]:
    """Locked-document reminder (4.2, post-roadmap phase 30 session 4,
    ADR 0111) - same "sent once per current deadline" dedup shape as
    `list_due_for_reminder` above, just keyed on `locked_at` (which
    `acquire_lock` restarts on every fresh lock AND every renewal by the
    same holder) instead of a fixed retention deadline."""
    threshold = datetime.now(UTC) - timedelta(seconds=threshold_seconds)
    result = await session.execute(
        select(DocumentLock).where(
            DocumentLock.locked_at <= threshold,
            DocumentLock.reminder_sent_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def list_due_for_retention_action(session: AsyncSession) -> list[Document]:
    """Documents with a due retention period (5.2/5.2a) without an active
    legal hold - `full_deletion` decides in the caller (main.py) whether
    a regular soft delete or physical forced deletion follows."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(Document).where(
            Document.retention_until.isnot(None),
            Document.retention_until <= now,
            Document.deleted_at.is_(None),
        )
    )
    candidates = list(result.scalars().all())
    return [d for d in candidates if not await has_active_hold(session, d.id)]


async def list_expired_trash(session: AsyncSession, *, restore_period_days: int) -> list[Document]:
    """Trash entries whose restore period has expired
    (5.2) - a legal hold also blocks routine cleanup."""
    deadline = datetime.now(UTC) - timedelta(days=restore_period_days)
    result = await session.execute(
        select(Document).where(Document.deleted_at.isnot(None), Document.deleted_at <= deadline)
    )
    candidates = list(result.scalars().all())
    return [d for d in candidates if not await has_active_hold(session, d.id)]


# --- Forensic trace: audit depth (5.4b, since P7-S2c) -----------------------


async def get_audit_trace_config(session: AsyncSession) -> AuditTraceConfig:
    """Reads the (single) base logging-depth row, creating it with
    defaults if it was never saved before (same pattern as
    `get_upload_config`) - default per user specification: both categories on."""
    config = await session.get(AuditTraceConfig, _AUDIT_TRACE_CONFIG_ID)
    if config is None:
        config = AuditTraceConfig(
            id=_AUDIT_TRACE_CONFIG_ID,
            log_viewed=True,
            log_downloaded=True,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_audit_trace_config(
    session: AsyncSession, *, log_viewed: bool, log_downloaded: bool
) -> AuditTraceConfig:
    config = await get_audit_trace_config(session)
    config.log_viewed = log_viewed
    config.log_downloaded = log_downloaded
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def list_role_overrides(session: AsyncSession) -> list[AuditTraceRoleOverride]:
    result = await session.execute(
        select(AuditTraceRoleOverride).order_by(AuditTraceRoleOverride.role)
    )
    return list(result.scalars().all())


async def upsert_role_override(
    session: AsyncSession, role: str, *, log_viewed: bool | None, log_downloaded: bool | None
) -> AuditTraceRoleOverride:
    override = await session.get(AuditTraceRoleOverride, role)
    if override is None:
        override = AuditTraceRoleOverride(role=role, updated_at=datetime.now(UTC))
        session.add(override)
    override.log_viewed = log_viewed
    override.log_downloaded = log_downloaded
    override.updated_at = datetime.now(UTC)
    await session.flush()
    return override


async def delete_role_override(session: AsyncSession, role: str) -> None:
    override = await session.get(AuditTraceRoleOverride, role)
    if override is None:
        raise NotFoundError(f"Kein Rollen-Override für {role!r}")
    await session.delete(override)
    await session.flush()


def resolve_should_log(
    category: str,
    roles: set[str],
    config: AuditTraceConfig,
    overrides: list[AuditTraceRoleOverride],
) -> bool:
    """Resolves whether a `document.viewed`/`document.downloaded` action for
    a caller with `roles` should be logged. `category` is
    `"viewed"` or `"downloaded"`. Conflict rule for multiple roles with
    contradictory overrides: logging wins (security-first) -
    see architecture decision in PROGRESS.md/docs."""
    field = "log_viewed" if category == "viewed" else "log_downloaded"
    matching = [o for o in overrides if o.role in roles and getattr(o, field) is not None]
    if not matching:
        return bool(getattr(config, field))
    values = [getattr(o, field) for o in matching]
    if any(values):
        return True
    return False


# --- Records disposal (5.6, since P7-S3) ----------------------------------------


async def list_due_for_archival(session: AsyncSession) -> list[Document]:
    """Documents with a due records disposal (5.6) - independent of
    `retention_until`/`full_deletion` (5.2), since records disposal, per the
    concept, is supplementary to the regular retention period. The
    `archival-service` polls this periodically (internal call,
    `GET /documents/due-for-archival`)."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(Document).where(
            Document.archive_after.isnot(None),
            Document.archive_after <= now,
            Document.archived_at.is_(None),
            Document.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def request_archive(session: AsyncSession, document_id: str) -> Document:
    """Manual records-disposal trigger (5.6, `POST /documents/{id}/archive-
    request`) - sets `archive_after` to now if not yet set
    or not yet due. A date that is already due/in the past remains
    unchanged (no turning back an already ongoing records disposal)."""
    document = await get_document(session, document_id)
    now = datetime.now(UTC)
    if document.archive_after is None or document.archive_after > now:
        document.archive_after = now
        document.updated_at = now
        await session.flush()
    return document


async def mark_archived(
    session: AsyncSession, document_id: str, *, archive_format: str
) -> Document:
    """Callback from `archival-service` once the archive copy has been
    verified (`PUT /documents/{id}/archived`) - the `Document` row itself
    remains fully intact (literal concept requirement, see models.py)."""
    document = await get_document(session, document_id)
    document.archived_at = datetime.now(UTC)
    document.archive_format = archive_format
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


async def mark_dehydrated(session: AsyncSession, document_id: str) -> Document:
    """Callback from `archival-service` after the live storage copy was
    removed once the transition period elapsed (`PUT /documents/{id}/
    dehydrated`)."""
    document = await get_document(session, document_id)
    document.dehydrated_at = datetime.now(UTC)
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


async def mark_rehydrated(session: AsyncSession, document_id: str) -> Document:
    """Callback from `archival-service` after successful retrieval (`PUT
    /documents/{id}/rehydrated`) - the live copy has been restored."""
    document = await get_document(session, document_id)
    document.dehydrated_at = None
    document.updated_at = datetime.now(UTC)
    await session.flush()
    return document


# --- Public share link (4.2a, P14-S10) ------------------------------


async def get_share_link_config(session: AsyncSession) -> ShareLinkConfig:
    config = await session.get(ShareLinkConfig, _SHARE_LINK_CONFIG_ID)
    if config is None:
        config = ShareLinkConfig(
            id=_SHARE_LINK_CONFIG_ID,
            enabled=True,
            max_validity_days=30,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_share_link_config(
    session: AsyncSession, *, enabled: bool, max_validity_days: int
) -> ShareLinkConfig:
    config = await get_share_link_config(session)
    config.enabled = enabled
    config.max_validity_days = max_validity_days
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def create_share_link(
    session: AsyncSession, *, document_id: str, created_by: str, expires_at: datetime
) -> ShareLink:
    # `token_urlsafe(32)` is at the same time the primary key - no separate,
    # guessable ID field alongside it with the same access power.
    link = ShareLink(
        token=secrets.token_urlsafe(32),
        document_id=document_id,
        created_by=created_by,
        created_at=datetime.now(UTC),
        expires_at=expires_at,
    )
    session.add(link)
    await session.flush()
    return link


async def list_share_links_for_document(session: AsyncSession, document_id: str) -> list[ShareLink]:
    result = await session.execute(
        select(ShareLink)
        .where(ShareLink.document_id == document_id)
        .order_by(ShareLink.created_at.desc())
    )
    return list(result.scalars().all())


async def get_share_link(session: AsyncSession, token: str) -> ShareLink | None:
    return await session.get(ShareLink, token)


async def revoke_share_link(session: AsyncSession, token: str, *, revoked_by: str) -> ShareLink:
    link = await get_share_link(session, token)
    if link is None:
        raise NotFoundError(f"Freigabelink {token!r} unbekannt")
    if link.revoked_at is None:
        link.revoked_at = datetime.now(UTC)
        link.revoked_by = revoked_by
        await session.flush()
    return link


def is_share_link_active(link: ShareLink, now: datetime) -> bool:
    return link.revoked_at is None and link.expires_at > now


# --- Direct Office editing (post-roadmap feature, WebDAV edit token) -----


async def create_webdav_edit_token(
    session: AsyncSession, *, document_id: str, principal_id: str, expires_at: datetime
) -> WebdavEditToken:
    token = WebdavEditToken(
        token=secrets.token_urlsafe(32),
        document_id=document_id,
        principal_id=principal_id,
        created_at=datetime.now(UTC),
        expires_at=expires_at,
    )
    session.add(token)
    await session.flush()
    return token


async def list_webdav_edit_tokens_for_document(
    session: AsyncSession, document_id: str
) -> list[WebdavEditToken]:
    result = await session.execute(
        select(WebdavEditToken)
        .where(WebdavEditToken.document_id == document_id)
        .order_by(WebdavEditToken.created_at.desc())
    )
    return list(result.scalars().all())


async def get_webdav_edit_token(session: AsyncSession, token: str) -> WebdavEditToken | None:
    return await session.get(WebdavEditToken, token)


async def revoke_webdav_edit_token(
    session: AsyncSession, token: str, *, revoked_by: str
) -> WebdavEditToken:
    edit_token = await get_webdav_edit_token(session, token)
    if edit_token is None:
        raise NotFoundError(f"WebDAV-Edit-Token {token!r} unbekannt")
    if edit_token.revoked_at is None:
        edit_token.revoked_at = datetime.now(UTC)
        edit_token.revoked_by = revoked_by
        await session.flush()
    return edit_token


def is_webdav_edit_token_active(token: WebdavEditToken, now: datetime) -> bool:
    return token.revoked_at is None and token.expires_at > now


async def get_export_config(session: AsyncSession) -> ExportConfig:
    config = await session.get(ExportConfig, _EXPORT_CONFIG_ID)
    if config is None:
        config = ExportConfig(
            id=_EXPORT_CONFIG_ID, history_position="after", updated_at=datetime.now(UTC)
        )
        session.add(config)
        await session.flush()
    return config


async def update_export_config(session: AsyncSession, *, history_position: str) -> ExportConfig:
    config = await get_export_config(session)
    config.history_position = history_position
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def list_documents_for_folder_export(session: AsyncSession, folder_id: str) -> list[Document]:
    """Document order for the combined folder export's table of contents
    (post-roadmap phase 28, ADR 0107) - by `Kennzeichen` (reference number)
    when set, this project's existing natural per-document ordering key
    (`object-type-service`'s `kennzeichen_format`), falling back to title
    for documents without one. `list_documents_by_folder`'s plain
    alphabetical-by-title order isn't a meaningful TOC order on its own."""
    documents = await list_documents_by_folder(session, folder_id)
    return sorted(documents, key=lambda d: (d.attributes.get("Kennzeichen") or "", d.title))


async def create_folder_export_job(
    session: AsyncSession, *, folder_id: str, history_position: str, created_by: str
) -> FolderExportJob:
    job = FolderExportJob(
        folder_id=folder_id,
        history_position=history_position,
        status="pending",
        created_by=created_by,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    return job


async def get_folder_export_job(session: AsyncSession, job_id: str) -> FolderExportJob:
    job = await session.get(FolderExportJob, job_id)
    if job is None:
        raise NotFoundError(f"Ordner-Export {job_id!r} unbekannt")
    return job


_ACTIVE_FOLDER_EXPORT_STATUSES = ("pending", "processing")


async def list_due_folder_export_jobs(session: AsyncSession) -> list[FolderExportJob]:
    """Jobs a poll tick should attempt (post-roadmap phase 28, ADR 0107) -
    same `next_retry_at` gating as `archival_service.repository.
    list_active_transfers` (post-roadmap phase 20 session 2, ADR 0078): a
    job that just received a backoff delay after a failed attempt is
    skipped until it elapses, instead of failing again on every tick."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(FolderExportJob).where(
            FolderExportJob.status.in_(_ACTIVE_FOLDER_EXPORT_STATUSES),
            or_(FolderExportJob.next_retry_at.is_(None), FolderExportJob.next_retry_at <= now),
        )
    )
    return list(result.scalars().all())


async def mark_folder_export_processing(session: AsyncSession, job: FolderExportJob) -> None:
    job.status = "processing"
    job.updated_at = datetime.now(UTC)
    await session.flush()


async def mark_folder_export_completed(
    session: AsyncSession, job: FolderExportJob, *, storage_object_key: str
) -> None:
    job.status = "completed"
    job.storage_object_key = storage_object_key
    job.error_message = None
    job.next_retry_at = None
    job.updated_at = datetime.now(UTC)
    await session.flush()


async def mark_folder_export_failed(
    session: AsyncSession, job: FolderExportJob, *, error_message: str, max_attempts: int
) -> None:
    """Same escalation shape as `archival_service.repository.mark_failed`
    (ADR 0078): stays in an active status with a backoff-delayed retry
    until `max_attempts` is exhausted, only then moves to the real terminal
    `failed_permanent`."""
    job.attempts += 1
    job.error_message = error_message
    job.updated_at = datetime.now(UTC)
    if job.attempts >= max_attempts:
        job.status = "failed_permanent"
        job.next_retry_at = None
    else:
        job.status = "pending"
        delay = compute_backoff_seconds(job.attempts - 1)
        job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
    await session.flush()


async def reset_folder_export_for_retry(session: AsyncSession, job: FolderExportJob) -> None:
    job.status = "pending"
    job.attempts = 0
    job.next_retry_at = None
    job.error_message = None
    job.updated_at = datetime.now(UTC)
    await session.flush()
