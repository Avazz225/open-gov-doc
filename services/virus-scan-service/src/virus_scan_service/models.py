from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("virus_scan")


class ScanResult(Base):
    """A performed scan (10.3). `document_id` is not yet known at the
    initial upload (the scan runs *before* document creation, ADR 0010) and
    is therefore nullable; it is already known when checking in a new
    version. Since P15-S2 (2.5), `document_id` is retroactively set to the
    newly created document on a release (status="released").
    """

    __tablename__ = "scan_result"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    # "clean" | "infected" | "released" (false positive cleared, 2.5, P15-S2)
    # | "purged" (permanently deleted, 2.5, P15-S2)
    status: Mapped[str] = mapped_column(String(16))
    threat_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    engine: Mapped[str] = mapped_column(String(32))
    # Only ever set for status="infected": object key in the Storage Service
    # under which the infected file was stored for evidentiary purposes
    # (quarantine instead of automatic deletion, 10.3). Remains as a
    # historical reference after release/permanent deletion, even though the
    # bytes themselves no longer exist by then - the field is not nulled out,
    # so `GET /scans/{id}` stays traceable (5.3).
    quarantine_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Who/when resolved the quarantine case (released or permanently
    # deleted, 2.5, P15-S2) - covers both actions with the same two columns,
    # analogous to the compact `deleted_by` pattern of the trash family
    # (P15-S1) instead of four separate release_*/purge_* columns.
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
