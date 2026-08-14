from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

# Schema name "case" is a reserved SQL keyword (CASE WHEN) - SQLAlchemy
# quotes it automatically in generated DDL statements (verified against
# PGIdentifierPreparer), whereas raw SQL strings in
# main.py/tests/conftest.py must quote it themselves as `"case"`. Table
# name deliberately "cases" (plural) instead of "case", to avoid needing
# this quoting requirement there as well.
Base = make_declarative_base("case")


class Case(Base):
    """Circulation folder (2.3): a standalone, object-type-capable object
    with its own lifecycle via a workflow-service process instance
    (P6-S1). `process_instance_id`'s `business_key` is deliberately
    identical to `id` (no separate field needed) - the basis for how
    `consumer.py` matches the completion of the associated process
    instance to a circulation folder."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    object_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    # "open" while being processed, "closed" after reaching the BPMN end
    # state (closure snapshot, see close_case in repository.py).
    status: Mapped[str] = mapped_column(String(32), default="open")
    process_definition_id: Mapped[int] = mapped_column(Integer)
    process_instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Records disposal (5.6, since P7-S3b) - `archive_after` is only
    # resolved on closure (see repository.close_case), not on creation like
    # `Document.archive_after`, since only closed circulation folders are
    # eligible for disposal.
    archive_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Case number (2.3/2.5, P15-S3): server-generated, installation-wide
    # unique reference (unlike `Document.attributes["Kennzeichen"]`, which
    # is only unique per object type + year) - basis for the new
    # `mail-connector`, which needs to automatically match incoming mail to
    # a circulation folder based on a token found in the subject/body.
    # Nullable, since it's only assigned for cases newly created from this
    # session onward (no retroactive backfill of existing rows).
    # Deliberately NOT changeable via PATCH (unlike the reference number) -
    # a stable, purely system-assigned reference is a prerequisite for
    # reliable matching.
    vorgangsnummer: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CaseDocumentReference(Base):
    """A circulation folder's reference to a document (2.3) - dynamic while
    processing is ongoing (version is resolved live from the Document
    Service on every read access, see repository.list_document_references),
    fixed in `snapshot_version_number` from the circulation folder's
    closure onward (closure snapshot). Removed references are soft-deleted
    (`removed_by`/`removed_at`) instead of hard-removed - for
    traceability."""

    __tablename__ = "case_document_reference"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(128), ForeignKey("case.cases.id"), index=True)
    document_id: Mapped[str] = mapped_column(String(128))
    added_by: Mapped[str] = mapped_column(String(128))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshot_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CaseArchivalConfig(Base):
    """Installation-wide records disposal configuration (5.6, since P7-S3b)
    - single row (`id=1`, same pattern as document-service's
    `RetentionConfig`). No counterpart to `ObjectType.default_archive_after_
    days`/`archive_encryption_enabled`: `ObjectType.applies_to` only knows
    `"document"`/`"folder"`, no own category for circulation folders - see
    docs/services/archival-service.md."""

    __tablename__ = "case_archival_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    default_archive_after_days_closed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    archive_encryption_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseNumberConfig(Base):
    """Installation-wide format string for the case number (2.3/2.5,
    P15-S3) - single row (`id=1`), same pattern as `CaseArchivalConfig`.
    Deliberately ONE installation-wide scheme instead of a per-object-type
    generator like for the reference number (object-type-service does not
    know "case" as its own `applies_to` category, see the comment on
    `CaseArchivalConfig` above) - for the purpose pursued here (reliable
    matching of incoming mail), a single, guaranteed-unique counter is
    sufficient."""

    __tablename__ = "case_number_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    format: Mapped[str] = mapped_column(String(64), default="{YYYY}-{Laufende_Nummer}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseSequence(Base):
    """Atomic, concurrency-safe yearly counter for the case number
    (P15-S3) - same idiom as object-type-service's `ObjectTypeSequence`
    (P5e-S1), here without an object-type dimension (one row per year
    instead of per object type + year)."""

    __tablename__ = "case_sequence"

    jahr: Mapped[int] = mapped_column(primary_key=True)
    naechste_nummer: Mapped[int] = mapped_column(default=1)
