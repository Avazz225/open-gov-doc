from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("object_type")


class ObjectType(Base):
    """Object type definition (2.2): attributes, naming conventions,
    conditional rules. The schema is not applied here, but via
    ``dms-constraint-engine`` in the ``/validate`` endpoint (see main.py)."""

    __tablename__ = "object_type"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    applies_to: Mapped[str] = mapped_column(String(16))  # "document" | "folder"
    attributes: Mapped[list] = mapped_column(JSON, default=list)
    naming_constraints: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    # Enforced object hierarchy (2.2a): names of allowed parent folder classes,
    # or the sentinel "$ROOT" for placement directly under the root. None/empty
    # = can be placed anywhere (backward compatibility for types without this).
    allowed_parent_types: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Only set for folder classes (applies_to == "folder") (2.2a) - display in
    # the user UI explorer before the name only follows with P5b-S4.
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Reference number generator (2.2, since P5e-S1) - only set for
    # applies_to == "document". None = no generator configured. Format string
    # with placeholders {YYYY}/{YY}/{MM}/{DD}/{Laufende_Nummer}, see
    # repository._render_kennzeichen.
    kennzeichen_format: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Tri-state override (None = no override, the global default from P5e-S2
    # applies) of the global "show reference number before filename" toggle.
    kennzeichen_display_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Minimum signature level (3.10, since P6-S7) - only set for
    # applies_to == "document" (folders are not signed). None = no
    # requirement. Checked by the Signature Service against the requested
    # level on every signing operation (`ses` < `aes` < `qes`), see
    # signature-service/main.py.
    required_signature_level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Retention (5.2, since P7-S1): default retention period in days from
    # creation, valid equally for applies_to == "document" AND "folder"
    # (applies to both Session P7-S1/P7-S1b). None = no type default, period
    # remains to be set manually.
    default_retention_days: Mapped[int | None] = mapped_column(nullable=True)
    # Tri-state override (None = no override, the global default from
    # RetentionConfig applies) - whether a deletion reason is mandatory on
    # forced deletion (5.2a), same pattern as kennzeichen_display_override.
    deletion_reason_required_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Records disposal (5.6, since P7-S3): default period in days from
    # creation, after which a document becomes eligible for disposal
    # (archival-service resolves Document.archive_after from this once) -
    # exactly the same pattern as default_retention_days, but independent of
    # it (per the concept, records disposal is complementary to the regular
    # retention period, not a third branch of the same decision). None = no
    # type default, only a manual trigger is possible.
    default_archive_after_days: Mapped[int | None] = mapped_column(nullable=True)
    # Whether the archive copy is encrypted before being stored at the
    # archive target (5.6) - via archival-service's KeyStore plugin
    # interface (ADR 0029), defaults to false (unencrypted).
    archive_encryption_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Classified-documents classification (2.5, P15-S1, multi-level instead of
    # binary since P17-S2 - 14.2) - only set for applies_to == "document" (the
    # concept text explicitly names only documents, not folders). None =
    # not classified, otherwise one of the four common German classification
    # levels (see schemas.ClassificationLevel). Every set value (regardless
    # of the specific level) still triggers the same binary gate as before:
    # a classified, deleted document ends up in the structurally separate
    # classified-documents trash instead of the regular one, see
    # document-service `settings.classified_trash_hard_delete_admin_role` -
    # the specific level is purely additional information (display/audit),
    # not additional role differentiation in this reference implementation.
    # Replaces the purely binary `is_classified: bool` from before P17-S1
    # (identical schema-bound architecture pattern, see ADR 0051
    # "Rationale" - only the value space was extended).
    classification_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObjectTypeSequence(Base):
    """Atomic, concurrency-safe yearly counter for the reference number
    generator (P5e-S1): one row per object type and year, so the counter
    automatically restarts at 1 at the turn of the year (user decision
    "reset per year", see PROGRESS.md)."""

    __tablename__ = "object_type_sequence"

    object_type_id: Mapped[int] = mapped_column(
        ForeignKey("object_type.object_type.id", ondelete="CASCADE"), primary_key=True
    )
    jahr: Mapped[int] = mapped_column(primary_key=True)
    naechste_nummer: Mapped[int] = mapped_column(default=1)


class KennzeichenConfig(Base):
    """Global display default for the reference number generator (2.2, since
    P5e-S3) - deliberately a single row with fixed ``id=1``, same pattern as
    ``OcrConfig``/``UploadConfig`` of the other services (one installation,
    no tenant separation, 3a). Individual document types can override this
    default via ``ObjectType.kennzeichen_display_override``."""

    __tablename__ = "kennzeichen_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    show_before_filename: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObjectTypeLayout(Base):
    """Form layout per object type and usage purpose (2.2b, since P5b-S2):
    ``purpose`` is ``"display"``|``"search"``|``"upload"``. Only layouts
    that explicitly deviate from the generated "smart layout" are persisted
    here (see ``object_type_service.layout.generate_smart_layout``) -
    without its own row, it is regenerated from the current attribute list
    on every read, so an unmodified default never goes stale."""

    __tablename__ = "object_type_layout"

    object_type_id: Mapped[int] = mapped_column(
        ForeignKey("object_type.object_type.id", ondelete="CASCADE"), primary_key=True
    )
    purpose: Mapped[str] = mapped_column(String(16), primary_key=True)
    layout: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
