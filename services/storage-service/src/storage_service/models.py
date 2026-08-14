from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

WRITE_STRATEGIES = ("quorum", "primary_async")

Base = make_declarative_base("storage")


class ObjectMetadata(Base):
    """Metadata per stored object - the actual content never lives in the
    shared DB, only reference, checksum, and size (concept 3.6)."""

    __tablename__ = "object_metadata"

    object_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    # Primary target `id` at the time of creation/last overwrite (3.6, no
    # FK to a backend directory - targets are pure settings, not their own
    # table). Since P5b-S6 a target `id` instead of a backend *type*
    # (multiple instances of the same type are now possible, ADR 0017).
    backend: Mapped[str] = mapped_column(String(64))
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObjectCopy(Base):
    """One row per object per configured redundancy target (3.6) - the
    basis for read-access fallback, per-copy fixity checks, and the retry
    queue for asynchronous replication. ``status``: ``pending`` (not yet
    replicated), ``ok``, ``failed`` (next process-pending run retries it),
    or ``failed_permanent`` (max_replication_attempts reached, see
    Settings). ``next_retry_at`` (since Post-Roadmap Phase 20 Session 6,
    ADR 0082): the original `process-pending` endpoint picked up every
    `failed` row again immediately on EVERY call, with no wait time at
    all - `libs/dms-retry`'s full-jitter backoff (same formula as the
    other four resilience spots from this phase) now delays the next
    eligible attempt; `NULL` means due immediately (new row or no failure
    yet)."""

    __tablename__ = "object_copy"

    object_key: Mapped[str] = mapped_column(
        String(1024), ForeignKey("storage.object_metadata.object_key"), primary_key=True
    )
    backend_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Retention/WORM (5.1/5.2a, since P7-S1): if this copy is still under
    # a retention period that lies in the future, `retention_guard` blocks
    # its deletion - independent of the backend type (including `local`,
    # which cannot have a real Object Lock). Only for targets with
    # `object_lock_mode` set in the Settings configuration is real S3
    # Object Lock additionally used.
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BackendIdentity(Base):
    """Last known device ID per configured target (3.6 "storage device
    swap sensitivity", P5b-S6). Deliberately a separate table instead of
    relying solely on the identity file read from the backend itself: a
    swapped/reset target may no longer have an identity file at all - the
    comparison value must therefore be stored independently of the target
    itself, see ADR 0017."""

    __tablename__ = "backend_identity"

    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GuardConfig(Base):
    """Admin-UI-editable runtime configuration of the redundancy guard
    (3.6, P5b-S6) - a single row with fixed `id=1`, same pattern as
    `OcrConfig` (ocr-service, P5b-S5/ADR 0016). Deliberately a
    **proactively** set standing policy ("if a storage device swap is
    ever detected, allow a degraded start"), not an emergency switch at
    the moment of an already-refused start - the service that would need
    to receive the approval wouldn't actually be running at that moment
    (see ADR 0017 for why this works anyway: Postgres itself is
    independent of a broken storage backend)."""

    __tablename__ = "guard_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    allow_degraded_start: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OperationalConfig(Base):
    """Admin-UI-editable write strategy/retry parameters (3.6, Post-
    Roadmap Phase 22 Session 6) - a single row with fixed `id=1`, same
    pattern as `GuardConfig`/`OcrConfig`. Unlike `Settings.targets` (env-
    only, credentials - deliberately remains immutable at runtime, see
    ADR 0091), these are pure operational parameters without secrets: read
    fresh from the DB on every access (no `app.state` cache), so they take
    effect without a restart. Seed values for the first row come from the
    previous env-var defaults (`Settings.write_strategy` etc.) - an
    already-running installation therefore does not change its behavior
    when upgrading to this session, until an admin deliberately calls
    `PUT /operational-config`."""

    __tablename__ = "operational_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    write_strategy: Mapped[str] = mapped_column(String(32))
    quorum_count: Mapped[int] = mapped_column(Integer)
    max_replication_attempts: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TargetOverride(Base):
    """Admin-UI-editable target metadata per already-configured backend
    (3.6, Post-Roadmap Phase 22 Session 7, ADR 0092) - ONLY
    `object_lock_mode`/`role`, NOT credentials/structure (`id`/`type`/
    `base_path`/... remain env-var-only, same rationale as
    `OperationalConfig`/ADR 0091: new targets require real infrastructure,
    not an admin-UI action). Deliberately **sparse** instead of a
    singleton with a full list: only targets with an actually set override
    have a row at all - if one is missing, the env-var value from
    `Settings.targets` still applies unchanged. `main.py`'s
    `_compute_target_state()` merges both sources on every call into an
    effective target list and writes the result immediately back to
    `app.state` (live reload without a restart, without every individual
    read access in the rest of the code having to read fresh from the DB
    itself)."""

    __tablename__ = "target_override"

    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    object_lock_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
