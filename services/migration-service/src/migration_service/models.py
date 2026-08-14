from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("migration")


class PairedInstallation(Base):
    """A target/source installation paired directly (without a hub, see the
    successor ADR to ADR 0033 in this session) (7.2). Unlike
    `federation-hub-service`'s `Installation` (which only stores a hash,
    since the hub never needs the plaintext key again), this table stores
    the key in **plaintext**: this installation must both PRESENT it on
    outgoing calls as the source and VERIFY it on incoming calls as the
    target - a pure hash would make the first role impossible. The same key
    is stored on both paired sides (transferred once manually by the admin,
    see `POST /paired-installations`)."""

    __tablename__ = "paired_installation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    base_url: Mapped[str] = mapped_column(String(512))
    api_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Transfer(Base):
    """A migration operation in which THIS installation is the source (7.2).
    State machine analogous to `archival-service`'s `ArchivalTransfer`, but
    here driven by a real BPMN workflow in `workflow-service`
    (`workflow_instance_id`), not by a dedicated poll loop - see
    docs/services/migration-service.md."""

    __tablename__ = "transfer"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_folder_id: Mapped[str] = mapped_column(String(64))
    target_installation_id: Mapped[str] = mapped_column(String(36))
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_days: Mapped[int] = mapped_column(Integer)
    # pending -> locked -> copied -> verified -> released -> deletion_scheduled -> deleted
    # (+ dry_run_completed, failed - reachable from any active intermediate status)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    workflow_instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scope_lock_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    documents_total: Mapped[int] = mapped_column(Integer, default=0)
    documents_copied: Mapped[int] = mapped_column(Integer, default=0)
    documents_verified: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    copied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InboundTransfer(Base):
    """This installation is the TARGET of a transfer triggered by a paired
    source (7.2) - deliberately a dedicated, lean table instead of reusing
    `Transfer`: the target side is a passive recipient without its own
    workflow orchestration, only needing the mapping
    `transfer_id -> local target folder` for the duration of the
    operation."""

    __tablename__ = "inbound_transfer"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_installation_id: Mapped[str] = mapped_column(String(36))
    target_folder_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
