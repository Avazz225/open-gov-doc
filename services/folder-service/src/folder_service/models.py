import uuid
from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("folder")


class Folder(Base):
    """Folder as a hierarchical container (2.1). Publishes structure events
    that the Permission Service consumes to keep its `ResourceNode` tree in
    sync (see docs/services/permission-service.md)."""

    __tablename__ = "folder"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    parent_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("folder.folder.id"), nullable=True, index=True
    )
    # Opaque reference to object-type-service - no FK enforcement across
    # service boundaries (analogous to document-service).
    object_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    # Retention/legal hold/forced deletion for folders (5.2/5.2a, since
    # P7-S1b) - exactly the same field-pair pattern as
    # `document_service.Document` (see P7-S1), here additionally extended
    # with the cascade origin.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who set the deletion marker (2.5, P15-S1) - prerequisite for the
    # personal trash, same retrofit gap found at P15-S0 as in
    # document-service (`deleted_by` was accepted but never persisted).
    deleted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Set when this folder was not deleted individually but as a cascade
    # because a parent folder was moved to trash - when restoring the
    # parent folder, `restore_folder` restores ONLY subfolders that were
    # cascaded this way.
    deleted_via_folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    full_deletion: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_deletion_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    deletion_reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reminder_notify_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    force_delete_approval_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FolderTemplate(Base):
    """Structure templates (2.5/7.3, since P15-S6) - a folder subtree as a
    named, reusable template (e.g. a file plan skeleton). `structure` is a
    nested tree ({"name", "object_type_id", "children"}) - deliberately
    captures ONLY structure (name + object type per node), no attribute
    values: a "skeleton" is only populated after being applied, see
    ADR 0056. No FK on `Folder` - a template remains valid independent of
    whether its original source folder continues to exist."""

    __tablename__ = "folder_template"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    structure: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LegalHold(Base):
    """Legal hold for folders (5.2, since P7-S1b) - structurally identical
    to `document_service.LegalHold` (P7-S1), but a standalone table in the
    `folder` schema instead of reuse across service boundaries.
    Active = ``released_at IS NULL``."""

    __tablename__ = "legal_hold"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    folder_id: Mapped[str] = mapped_column(String(128), ForeignKey("folder.folder.id"), index=True)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    set_by: Mapped[str] = mapped_column(String(128))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeletionRegisterEntry(Base):
    """Deletion register for folders (5.2a, since P7-S1b) - structurally
    identical to `document_service.DeletionRegisterEntry`. Deliberately no
    FK on ``Folder.id`` - the deleted folder no longer exists at this
    point."""

    __tablename__ = "deletion_register_entry"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    folder_id: Mapped[str] = mapped_column(String(128), index=True)
    # "forced_deletion" | "trash_expiry" | "manual_purge" (the latter since P15-S1)
    trigger: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RetentionConfig(Base):
    """Admin-UI-editable base retention settings for folders (5.2/5.2a,
    since P7-S1b) - separate configuration, independent of
    `document_service.RetentionConfig` (an installation operator may need
    different rules for folders than for documents)."""

    __tablename__ = "retention_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deletion_reason_required: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_lead_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Admin-curated suggestions for the free-text `reason` field on
    # `PUT .../retention` (post-roadmap phase 31 session 1, ADR 0112) - same
    # UX-only mechanism as document-service's own copy of this field.
    deletion_reason_catalog: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrashConfig(Base):
    """Trash restore period for folders (5.2, since P7-S1b) - separate
    configuration, independent of `document_service.TrashConfig`."""

    __tablename__ = "trash_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restore_period_days: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
