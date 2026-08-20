import uuid
from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("document")


class Document(Base):
    """Core entity (2.1). ``folder_id`` is an opaque reference to the
    Folder Service (P3-S3), ``object_type_id`` references the Object-Type
    Service (2.2) - both without FK enforcement across service boundaries,
    but actively checked against the respective service API since P3-S3
    (see main.py: existence of the folder, constraint validation of the
    attributes)."""

    __tablename__ = "document"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    object_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Custom attributes according to the object-type schema (2.2) -
    # only set/validated at creation time, no update endpoint yet for later
    # changes.
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    # Points to the current main version (not the most recently
    # created row - conflict copies do not advance this pointer, see
    # repository.checkin_version).
    current_version_number: Mapped[int] = mapped_column(Integer, default=0)
    # Process-specific working copy (2.3, P6-S3, e.g. a redaction
    # for file inspection): such a copy is a completely normal, independent
    # document with its own versioning/auditing, it only additionally
    # carries these three opaque provenance fields - no FK across service
    # boundaries, `derived_from_document_id` refers to `Document.id` within
    # the same schema and is therefore modeled as a genuine FK.
    derived_from_document_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("document.document.id"), nullable=True
    )
    derived_from_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    originating_case_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who set the deletion marker (2.5, P15-S1) - prerequisite for
    # the personal trash ("only the items I marked myself"). Until now
    # `deleted_by` was accepted in several places, but never actually
    # persisted (a genuine gap found during P15-S1).
    deleted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Cascade provenance (5.2, since P7-S1b): set when this document
    # was deleted not individually, but because its parent folder was moved
    # to the trash (`POST /documents/cascade-trash`) - when restoring the
    # folder, `POST /documents/cascade-restore` only restores documents
    # cascaded this way, not an independently, individually deleted document
    # in the same folder.
    deleted_via_folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Retention/forced deletion (5.2/5.2a, since P7-S1): a shared
    # field pair for both concept sections - once `retention_until` is
    # reached, `full_deletion=False` triggers the existing soft delete
    # (`deleted_at` above), `full_deletion=True` instead triggers immediate
    # physical deletion (see `_retention_poll_loop` in main.py). Set
    # manually or copied once from `ObjectType.default_retention_days`.
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    full_deletion: Mapped[bool] = mapped_column(Boolean, default=False)
    # Deletion reason (5.2a) - held on the document only during the
    # planning phase (necessary because execution happens days/weeks later).
    # On actual physical deletion, the reason migrates into
    # `DeletionRegisterEntry`/the audit event, the `Document` row itself is
    # completely removed in the process - so the reason only survives the
    # object there, never as a property of a still-existing object.
    pending_deletion_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Deletion reminder (5.2a, optional) - `deletion_reminder_sent_at`
    # prevents duplicate sending across successive poll runs.
    # `reminder_notify_email` is an opaque value supplied when scheduling -
    # same pattern as `escalation_email`/`notify_email` in workflow-service
    # (no role/account resolution).
    deletion_reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reminder_notify_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Prevents the poll loop, when the four-eyes principle (4.3) is
    # enabled, from creating a new approval request on every run for the
    # same due forced deletion while approval is still pending.
    force_delete_approval_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Records disposal (5.6, since P7-S3): copied once from
    # `ObjectType.default_archive_after_days` (analogous to
    # `retention_until`), independent of the regular retention period -
    # according to the concept, records disposal is supplementary, not a
    # third branch of the same decision. `None` = no type default, only a
    # manual trigger (`POST .../archive-request`) is possible.
    # `archived_at`/`archive_format` are set by `archival-service` via
    # callback once the archive copy has been verified; `dehydrated_at` once
    # the live copy has been removed after the transition period - the
    # `Document` row itself remains in place (metadata remains findable, a
    # literal requirement of the concept).
    archive_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    dehydrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Draft / pre-registration lifecycle (post-roadmap phase 31 session 2,
    # ADR 0113): `None` while a document is a draft - created with
    # `draft=True`, no `Kennzeichen` assigned yet, excluded from anything
    # that assumes a real reference number (mail-connector matching,
    # `list_documents_by_kennzeichen`, both naturally, since the attribute
    # key is simply absent). Set once, at creation time for a regular
    # (non-draft) document or via `POST .../register` for a former draft -
    # never reset afterwards (mirrors real-world reference-number issuance,
    # which is likewise never revoked once handed out).
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Base):
    """All versions remain permanently preserved (2.1a) - even
    conflict copies (``is_conflict=True``) are independent, forever
    retrievable rows, not transient intermediate states."""

    __tablename__ = "document_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("document.document.id"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    storage_object_key: Mapped[str] = mapped_column(String(1024))
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    is_conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    based_on_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UploadConfig(Base):
    """Admin-UI-editable format whitelist (3.1/3.6, P5d-S1) - same
    single-row pattern as `ocr_service.OcrConfig`/`storage_service.GuardConfig`:
    deliberately one fixed row `id=1` instead of genuine multi-row support
    (exactly one installation per deployment, 3a). Empty list (default) = no
    restriction - every content type detected via sniffing is accepted,
    identical to the behavior before P5d-S1."""

    __tablename__ = "upload_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    allowed_content_types: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentLock(Base):
    """Editing lock (4.2), bound to user + session. Exactly one
    active lock per document - hence ``document_id`` as the primary key
    instead of a dedicated ID/history."""

    __tablename__ = "document_lock"

    document_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("document.document.id"), primary_key=True
    )
    locked_by: Mapped[str] = mapped_column(String(128))
    session_id: Mapped[str] = mapped_column(String(128))
    # Version on which editing is based - basis for optimistic
    # conflict detection at check-in (see repository.checkin_version).
    based_on_version_number: Mapped[int] = mapped_column(Integer)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Locked-document reminder (post-roadmap phase 30 session 4, ADR 0111) -
    # same "sent once per current deadline, reset on change" dedup idiom as
    # `Document.deletion_reminder_sent_at`: reset to `None` on every
    # `acquire_lock` call (fresh lock AND renewal by the same holder both
    # restart `locked_at`, so the reminder clock restarts with it).
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LegalHold(Base):
    """Legal hold (5.2, since P7-S1): locks a document independently
    of its regular retention period, until the hold is explicitly released
    - overrides both the regular soft delete and forced deletion in the
    poll loop (see main.py). Active = ``released_at IS NULL``. A dedicated
    row instead of a single field on ``Document``, since the history
    (who/when set/released) is itself audit-relevant - multiple past holds
    per document remain traceable this way."""

    __tablename__ = "legal_hold"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("document.document.id"), index=True
    )
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    set_by: Mapped[str] = mapped_column(String(128))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeletionRegisterEntry(Base):
    """Deletion register (5.2a, since P7-S1): one entry per physical
    deletion actually carried out - both the planned forced deletion
    (``trigger="forced_deletion"``) and the routine trash retention-period
    expiry cleanup (``trigger="trash_expiry"``) create a row here.
    Deliberately NO FK on ``Document.id`` - the deleted document no longer
    exists at this point, the register is the only remaining evidence (see
    concept: reconciliation after backup restoration, checking whether
    objects deleted in the meantime came back unintentionally). Not
    hash-chained itself like audit-service - each row is additionally
    published as a regular event, which is recorded there as well (see
    docs/services/document-service.md "Open Points" on the still-missing
    separate backup policy, phase 11)."""

    __tablename__ = "deletion_register_entry"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    # "forced_deletion" | "trash_expiry" | "manual_purge" (the latter since P15-S1)
    trigger: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RetentionConfig(Base):
    """Admin-UI-editable base retention settings (5.2/5.2a, since
    P7-S1) - same single-row pattern as ``UploadConfig``."""

    __tablename__ = "retention_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Installation-wide default, overridable per ObjectType via
    # ``deletion_reason_required_override`` (tri-state).
    deletion_reason_required: Mapped[bool] = mapped_column(Boolean, default=False)
    # None = no deletion reminder (factory setting, "used rather
    # rarely" according to the concept).
    reminder_lead_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Admin-curated suggestions for the free-text `reason` field on
    # `PUT .../retention` (post-roadmap phase 31 session 1, ADR 0112) - a
    # UX convenience, not an enum constraint: the API still accepts any
    # non-empty string, this list only powers the frontend's dropdown (plus
    # an always-available "Sonstiges" choice that opens free text, never
    # stored in this list itself).
    deletion_reason_catalog: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrashConfig(Base):
    """Trash restoration period (5.2, since P7-S1) - same single-row
    pattern as ``UploadConfig``/``RetentionConfig``."""

    __tablename__ = "trash_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restore_period_days: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditTraceConfig(Base):
    """Base logging depth for the forensic trace (5.4b, since
    P7-S2c) - same single-row pattern as ``UploadConfig``. Controls whether
    `GET /documents/{id}` (`document.viewed`) or the content download
    endpoints (`document.downloaded`) publish an event at all. Overridable
    per role via ``AuditTraceRoleOverride``. Default per user requirement:
    both on (maximum traceability as the factory setting)."""

    __tablename__ = "audit_trace_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_viewed: Mapped[bool] = mapped_column(Boolean, default=True)
    log_downloaded: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditTraceRoleOverride(Base):
    """Role-specific override of the base logging depth (5.4b,
    since P7-S2c) - ``role`` is free text (no existence check against
    permission-service/Keycloak, same existing gap as everywhere else in
    the system). ``None`` per field means "base applies", a set value
    overrides the base for callers with this role. When multiple assigned
    roles have conflicting overrides, "log" wins (see
    main._resolve_should_log) - security-first, a role that demands more
    logging cannot be silently undermined by another role of the same
    user."""

    __tablename__ = "audit_trace_role_override"

    role: Mapped[str] = mapped_column(String(128), primary_key=True)
    log_viewed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    log_downloaded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ShareLinkConfig(Base):
    """Public share link (4.2a, P14-S10) - installation-wide switch,
    same single-row pattern as ``UploadConfig``/``RetentionConfig``.
    ``enabled=False`` according to the concept's literal wording means not
    just "block server-side" but "API route unreachable" - implemented as
    `404` instead of `403` on the affected endpoints (main.py), since
    genuinely not registering the route at runtime without a restart would
    not be possible. Default deliberately chosen as an implementation
    decision of this session (the concept itself does not commit to a
    factory setting, see ADR 0047)."""

    __tablename__ = "share_link_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_validity_days: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ShareLink(Base):
    """A single share link (4.2a) - ``token`` is the primary key AND
    the value used in the public URL (``secrets.token_urlsafe``, see
    repository.py) - deliberately no separate, guessable ID field alongside
    it that would have the same access power. Read-only access to EXACTLY
    this one document (no folder/metadata access beyond that, per the
    concept's literal wording) - ``document_id`` is the only business
    reference, no folder ID is ever stored here."""

    __tablename__ = "share_link"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Mandatory (concept wording: "no permanently valid link") - no
    # `nullable=True` like for retention periods, which may deliberately be
    # unlimited.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class WebdavEditToken(Base):
    """Short-lived token scoped to exactly one document, for direct
    Office editing from the browser (post-roadmap feature, Office URI
    scheme `ms-word:ofe|u|<url>` against `webdav-connector`) - structurally
    almost identical to `ShareLink` (`token` is again the PK AND the value
    used in the start URL, `secrets.token_urlsafe`), but unlike `ShareLink`
    NOT purely read-only: `principal_id` is the identity that
    `webdav-connector`'s `DmsAuthDomainController` inserts as
    `environ["wsgidav.auth.user_name"]` upon successful resolution, and
    which is therefore used as `created_by`/lock owner during the later
    check-in (`POST /documents/{id}/versions`) - a share link never needs
    this, since it never writes. `expires_at` is deliberately sized more
    generously than for `ShareLink` (hours instead of minutes, see
    `settings.webdav_edit_token_ttl_hours`), since an Office editing session
    makes WebDAV requests (lock/read/cache/unlock) throughout the entire
    duration, not just when opening."""

    __tablename__ = "webdav_edit_token"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    principal_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ExportConfig(Base):
    """Installation-wide default for the PDF export feature (post-roadmap
    phase 28, ADR 0107) - same single-row pattern as `ShareLinkConfig`/
    `UploadConfig`. `history_position` controls whether a document's export
    history section is appended after or prepended before the document
    content itself; an optional `?history_position=` query param on
    `POST /documents/{id}/export` overrides this default per call without
    changing it."""

    __tablename__ = "export_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    history_position: Mapped[str] = mapped_column(String(16), default="after")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FolderExportJob(Base):
    """Combined-folder PDF export (post-roadmap phase 28, ADR 0107) - runs
    as a background job instead of a synchronous request, since converting
    every contained document via LibreOffice and merging the results can
    take long for a folder with many documents. Same resilience shape as
    `ArchivalTransfer` (post-roadmap phase 20 session 2, ADR 0078):
    `attempts`/`next_retry_at` drive full-jitter backoff between attempts,
    `failed_permanent` is the real terminal failure state (not a bare
    `failed`) once `max_folder_export_attempts` is exhausted."""

    __tablename__ = "folder_export_job"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    folder_id: Mapped[str] = mapped_column(String(128), index=True)
    history_position: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    storage_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
