from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("document")


class Document(Base):
    """Kernentität (2.1). ``folder_id`` ist eine opake Referenz auf den
    Folder Service (P3-S3), ``object_type_id`` referenziert den Object-Type
    Service (2.2) - beide ohne FK-Erzwingung über Service-Grenzen hinweg,
    aber seit P3-S3 aktiv gegen die jeweilige Service-API geprüft (siehe
    main.py: Existenz des Ordners, Constraint-Validierung der Attribute)."""

    __tablename__ = "document"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    object_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Custom-Attribute gemäß Objekttyp-Schema (2.2) - nur bei Erstellung
    # gesetzt/validiert, noch kein Update-Endpunkt für spätere Änderungen.
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    # Zeigt auf die aktuelle Hauptversion (nicht die zuletzt angelegte Zeile -
    # Konfliktkopien erhöhen diesen Zeiger nicht, siehe repository.checkin_version).
    current_version_number: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Base):
    """Alle Versionen bleiben dauerhaft erhalten (2.1a) - auch Konfliktkopien
    (``is_conflict=True``) sind eigenständige, für immer abrufbare Zeilen,
    keine flüchtigen Zwischenstände."""

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


class DocumentLock(Base):
    """Bearbeitungssperre (4.2), an Nutzer + Session gebunden. Genau eine
    aktive Sperre je Dokument - deshalb ``document_id`` als Primärschlüssel
    statt einer eigenen ID/Historie."""

    __tablename__ = "document_lock"

    document_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("document.document.id"), primary_key=True
    )
    locked_by: Mapped[str] = mapped_column(String(128))
    session_id: Mapped[str] = mapped_column(String(128))
    # Version, auf der die Bearbeitung basiert - Grundlage der optimistischen
    # Konflikterkennung beim Check-in (siehe repository.checkin_version).
    based_on_version_number: Mapped[int] = mapped_column(Integer)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
