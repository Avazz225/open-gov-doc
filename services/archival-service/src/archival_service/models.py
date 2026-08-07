import uuid
from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("archival")


class ArchivalTransfer(Base):
    """Resumable Zustandsmaschine fuer die Aussonderung eines einzelnen
    Dokuments (5.6): `pending -> locked -> copied -> verified -> released
    -> dehydrated` (+ `failed`). Jede Phase wird einzeln committet, bevor der
    naechste Schritt beginnt - gleiches Poll-Loop-Idiom wie document-
    service's `_retention_poll_loop`/workflow-service's `_sla_poll_loop`
    (kein echter BPMN-Prozess, siehe docs/services/archival-service.md).
    Ein Absturz mitten im Ablauf wird beim naechsten Tick am persistierten
    `status` fortgesetzt, nicht neu gestartet."""

    __tablename__ = "archival_transfer"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    archive_format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    copied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dehydrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rehydrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseArchivalTransfer(Base):
    """Resumable Zustandsmaschine fuer die XDOMEA-Aussonderung einer
    geschlossenen Umlaufmappe (5.6, seit P7-S3b): `pending -> locked ->
    packaged -> verified -> released` (+ `failed`) - kein `dehydrated`-Status
    wie bei `ArchivalTransfer`, da ein Case keinen eigenen Live-Inhalt
    besitzt, der entfernt werden koennte (nur Referenzen auf Dokumente mit
    eigenem, unabhaengigem Lebenszyklus). Gleiches Poll-Loop-Idiom wie
    `ArchivalTransfer` oben, siehe docs/services/archival-service.md."""

    __tablename__ = "case_archival_transfer"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    packaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
