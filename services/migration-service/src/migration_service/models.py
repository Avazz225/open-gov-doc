from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("migration")


class PairedInstallation(Base):
    """Eine direkt (ohne Hub, siehe ADR 0033-Nachfolge-ADR dieser Session)
    gepaarte Ziel-/Quell-Installation (7.2). Anders als `federation-hub-
    service`s `Installation` (die nur einen Hash speichert, da der Hub den
    Klartext-Key nie wieder braucht) speichert diese Tabelle den Key im
    **Klartext**: diese Installation muss ihn sowohl beim ausgehenden Aufruf
    als Quelle PRÄSENTIEREN als auch beim eingehenden Aufruf als Ziel
    VERIFIZIEREN - ein reiner Hash würde die erste Rolle unmöglich machen.
    Derselbe Schlüssel wird auf beiden gepaarten Seiten hinterlegt (vom Admin
    einmalig manuell übertragen, siehe `POST /paired-installations`)."""

    __tablename__ = "paired_installation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    base_url: Mapped[str] = mapped_column(String(512))
    api_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Transfer(Base):
    """Ein Migrations-Vorgang, bei dem DIESE Installation die Quelle ist
    (7.2). Zustandsmaschine analog zu `archival-service`s `ArchivalTransfer`,
    hier aber über einen echten BPMN-Workflow in `workflow-service`
    vorangetrieben (`workflow_instance_id`), nicht über einen eigenen
    Poll-Loop - siehe docs/services/migration-service.md."""

    __tablename__ = "transfer"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_folder_id: Mapped[str] = mapped_column(String(64))
    target_installation_id: Mapped[str] = mapped_column(String(36))
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_days: Mapped[int] = mapped_column(Integer)
    # pending -> locked -> copied -> verified -> released -> deletion_scheduled -> deleted
    # (+ dry_run_completed, failed - erreichbar aus jedem aktiven Zwischenstatus)
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
    """Diese Installation ist das ZIEL eines von einer gepaarten Quelle
    ausgelösten Transfers (7.2) - bewusst eine eigene, schlanke Tabelle statt
    Wiederverwendung von `Transfer`: die Zielseite ist ein passiver Empfänger
    ohne eigene Workflow-Orchestrierung, braucht nur die Zuordnung
    `transfer_id -> lokaler Zielordner` für die Dauer des Vorgangs."""

    __tablename__ = "inbound_transfer"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_installation_id: Mapped[str] = mapped_column(String(36))
    target_folder_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
