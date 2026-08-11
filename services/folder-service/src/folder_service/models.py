import uuid
from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("folder")


class Folder(Base):
    """Ordner als hierarchischer Container (2.1). Publiziert Struktur-Events,
    die der Permission Service konsumiert, um seinen `ResourceNode`-Baum
    synchron zu halten (siehe docs/services/permission-service.md)."""

    __tablename__ = "folder"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    parent_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("folder.folder.id"), nullable=True, index=True
    )
    # Opake Referenz auf object-type-service - keine FK-Erzwingung über
    # Service-Grenzen hinweg (analog zu document-service).
    object_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    # Aufbewahrung/Legal Hold/Zwangslöschung für Ordner (5.2/5.2a, seit
    # P7-S1b) - 1:1 dasselbe Feldpaar-Muster wie `document_service.Document`
    # (siehe P7-S1), hier zusätzlich um die Kaskaden-Herkunft ergänzt.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Wer die Löschmarkierung gesetzt hat (2.5, P15-S1) - Voraussetzung für den
    # persönlichen Papierkorb, gleiche, bei P15-S0 gefundene Nachrüst-Lücke
    # wie bei document-service (`deleted_by` wurde entgegengenommen, aber nie
    # persistiert).
    deleted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Gesetzt, wenn dieser Ordner nicht einzeln, sondern weil ein
    # übergeordneter Ordner in den Papierkorb verschoben wurde, mitgelöscht
    # wurde - `restore_folder` stellt beim Wiederherstellen des Elternordners
    # NUR dadurch kaskadierte Unterordner wieder her.
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
    """Struktur-Vorlagen (2.5/7.3, seit P15-S6) - ein Ordner-Teilbaum als
    benannte, wiederverwendbare Vorlage (z. B. Aktenplan-Rohbau). `structure`
    ist ein verschachtelter Baum ({"name", "object_type_id", "children"}) -
    erfasst bewusst NUR Struktur (Name + Objekttyp je Knoten), keine
    Attributwerte: ein "Rohbau" wird nach dem Anwenden erst befüllt, siehe
    ADR 0056. Kein FK auf `Folder` - eine Vorlage bleibt unabhängig vom
    Fortbestehen ihres ursprünglichen Quellordners gültig."""

    __tablename__ = "folder_template"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    structure: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LegalHold(Base):
    """Legal Hold für Ordner (5.2, seit P7-S1b) - strukturgleich zu
    `document_service.LegalHold` (P7-S1), aber eigenständige Tabelle im
    `folder`-Schema statt einer Wiederverwendung über Service-Grenzen
    hinweg. Aktiv = ``released_at IS NULL``."""

    __tablename__ = "legal_hold"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    folder_id: Mapped[str] = mapped_column(String(128), ForeignKey("folder.folder.id"), index=True)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    set_by: Mapped[str] = mapped_column(String(128))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeletionRegisterEntry(Base):
    """Löschregister für Ordner (5.2a, seit P7-S1b) - strukturgleich zu
    `document_service.DeletionRegisterEntry`. Bewusst kein FK auf
    ``Folder.id`` - der gelöschte Ordner existiert zu diesem Zeitpunkt
    bereits nicht mehr."""

    __tablename__ = "deletion_register_entry"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    folder_id: Mapped[str] = mapped_column(String(128), index=True)
    # "forced_deletion" | "trash_expiry" | "manual_purge" (letzteres seit P15-S1)
    trigger: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RetentionConfig(Base):
    """Admin-UI-editierbare Aufbewahrungs-Grundeinstellungen für Ordner
    (5.2/5.2a, seit P7-S1b) - eigene Konfiguration, unabhängig von
    `document_service.RetentionConfig` (ein Installationsbetreiber kann für
    Ordner andere Vorgaben brauchen als für Dokumente)."""

    __tablename__ = "retention_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deletion_reason_required: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_lead_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrashConfig(Base):
    """Papierkorb-Wiederherstellungsfrist für Ordner (5.2, seit P7-S1b) -
    eigene Konfiguration, unabhängig von `document_service.TrashConfig`."""

    __tablename__ = "trash_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restore_period_days: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
