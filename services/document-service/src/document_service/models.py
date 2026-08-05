import uuid
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
    # Prozessspezifische Bearbeitungskopie (2.3, P6-S3, z. B. eine Schwärzung
    # für die Akteneinsicht): eine solche Kopie ist ein ganz normales, eigenes
    # Dokument mit eigener Versionierung/Auditierung, trägt nur zusätzlich
    # diese drei opaken Herkunftsfelder - kein FK über Service-Grenzen hinweg,
    # `derived_from_document_id` verweist auf `Document.id` innerhalb
    # desselben Schemas und wird daher als echter FK modelliert.
    derived_from_document_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("document.document.id"), nullable=True
    )
    derived_from_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    originating_case_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Kaskaden-Herkunft (5.2, seit P7-S1b): gesetzt, wenn dieses Dokument nicht
    # einzeln, sondern weil sein übergeordneter Ordner in den Papierkorb
    # verschoben wurde (`POST /documents/cascade-trash`) gelöscht wurde -
    # `POST /documents/cascade-restore` stellt beim Wiederherstellen des
    # Ordners NUR dadurch kaskadierte Dokumente wieder her, kein unabhängig
    # einzeln gelöschtes Dokument im selben Ordner.
    deleted_via_folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Aufbewahrung/Zwangslöschung (5.2/5.2a, seit P7-S1): ein gemeinsames
    # Feldpaar für beide Konzeptabschnitte - `retention_until` erreicht,
    # `full_deletion=False` löst den bestehenden Soft-Delete aus (`deleted_at`
    # oben), `full_deletion=True` löst stattdessen die sofortige physische
    # Löschung aus (siehe `_retention_poll_loop` in main.py). Manuell gesetzt
    # oder einmalig aus `ObjectType.default_retention_days` übernommen.
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    full_deletion: Mapped[bool] = mapped_column(Boolean, default=False)
    # Löschgrund (5.2a) - nur während der Planungsphase auf dem Dokument
    # gehalten (notwendig, da die Ausführung Tage/Wochen später erfolgt).
    # Bei tatsächlicher physischer Löschung wandert der Grund in
    # `DeletionRegisterEntry`/das Audit-Event, die `Document`-Zeile selbst
    # wird dabei komplett entfernt - der Grund überlebt das Objekt also nur
    # dort, nie als Property eines noch existierenden Objekts.
    pending_deletion_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Löscherinnerung (5.2a, optional) - `deletion_reminder_sent_at`
    # verhindert Mehrfachversand über aufeinanderfolgende Poll-Durchläufe
    # hinweg. `reminder_notify_email` ist ein opakes, beim Terminieren
    # mitgegebenes Datum - gleiches Muster wie `escalation_email`/
    # `notify_email` bei workflow-service (keine Rollen-/Konto-Auflösung).
    deletion_reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reminder_notify_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Verhindert, dass der Poll-Loop bei aktiviertem Vier-Augen-Prinzip
    # (4.3) bei jedem Durchlauf erneut einen Freigabe-Request für dieselbe
    # fällige Zwangslöschung anlegt, solange die Genehmigung noch aussteht.
    force_delete_approval_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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


class UploadConfig(Base):
    """Admin-UI-editierbare Format-Whitelist (3.1/3.6, P5d-S1) - gleiches
    Einzelzeilen-Muster wie `ocr_service.OcrConfig`/`storage_service.GuardConfig`:
    bewusst eine feste Zeile `id=1` statt echter Mehrzeiligkeit (genau eine
    Installation je Deployment, 3a). Leere Liste (Default) = keine
    Einschränkung - jeder per Sniffing erkannte Content-Type wird akzeptiert,
    identisch zum Verhalten vor P5d-S1."""

    __tablename__ = "upload_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    allowed_content_types: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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


class LegalHold(Base):
    """Legal Hold (5.2, seit P7-S1): sperrt ein Dokument unabhängig von
    dessen regulärer Aufbewahrungsfrist, bis der Hold explizit aufgehoben
    wird - überschreibt sowohl regulären Soft-Delete als auch Zwangslöschung
    im Poll-Loop (siehe main.py). Aktiv = ``released_at IS NULL``. Eigene
    Zeile statt eines einzelnen Felds auf ``Document``, da die Historie
    (wer/wann gesetzt/aufgehoben) selbst auditrelevant ist - mehrere
    vergangene Holds je Dokument bleiben so nachvollziehbar."""

    __tablename__ = "legal_hold"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("document.document.id"), index=True
    )
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    set_by: Mapped[str] = mapped_column(String(128))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeletionRegisterEntry(Base):
    """Löschregister (5.2a, seit P7-S1): ein Eintrag je tatsächlich
    durchgeführter physischer Löschung - sowohl die geplante Zwangslöschung
    (``trigger="forced_deletion"``) als auch die routinemäßige
    Papierkorb-Fristablauf-Bereinigung (``trigger="trash_expiry"``) legen
    hier eine Zeile an. Bewusst KEIN FK auf ``Document.id`` - das gelöschte
    Dokument existiert zu diesem Zeitpunkt bereits nicht mehr, das Register
    ist der einzige verbleibende Nachweis (siehe Konzept: Abgleich nach
    Backup-Wiederherstellung, ob zwischenzeitlich gelöschte Objekte
    unbeabsichtigt zurückkamen). Nicht selbst hash-verkettet wie
    audit-service - jede Zeile wird zusätzlich als reguläres Event
    publiziert, das dort mitgeschrieben wird (siehe docs/services/
    document-service.md "Offene Punkte" zur noch fehlenden separaten
    Backup-Politik, Phase 11)."""

    __tablename__ = "deletion_register_entry"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    trigger: Mapped[str] = mapped_column(String(32))  # "forced_deletion" | "trash_expiry"
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RetentionConfig(Base):
    """Admin-UI-editierbare Aufbewahrungs-Grundeinstellungen (5.2/5.2a, seit
    P7-S1) - gleiches Einzelzeilen-Muster wie ``UploadConfig``."""

    __tablename__ = "retention_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Installationsweiter Default, je ObjectType per
    # ``deletion_reason_required_override`` überschreibbar (Tri-State).
    deletion_reason_required: Mapped[bool] = mapped_column(Boolean, default=False)
    # None = keine Löscherinnerung (Werkseinstellung, "eher seltener genutzt"
    # laut Konzept).
    reminder_lead_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrashConfig(Base):
    """Papierkorb-Wiederherstellungsfrist (5.2, seit P7-S1) - gleiches
    Einzelzeilen-Muster wie ``UploadConfig``/``RetentionConfig``."""

    __tablename__ = "trash_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restore_period_days: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditTraceConfig(Base):
    """Basis-Protokollierungstiefe für den Forensik-Trace (5.4b, seit
    P7-S2c) - gleiches Einzelzeilen-Muster wie ``UploadConfig``. Steuert, ob
    `GET /documents/{id}` (`document.viewed`) bzw. die Content-Download-
    Endpunkte (`document.downloaded`) überhaupt ein Event publizieren.
    Per-Rolle über ``AuditTraceRoleOverride`` überschreibbar. Default laut
    Nutzervorgabe: beide an (maximale Nachvollziehbarkeit als Werkseinstellung)."""

    __tablename__ = "audit_trace_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_viewed: Mapped[bool] = mapped_column(Boolean, default=True)
    log_downloaded: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditTraceRoleOverride(Base):
    """Rollenspezifischer Override der Basis-Protokollierungstiefe (5.4b,
    seit P7-S2c) - ``role`` ist freier Text (keine Existenzprüfung gegen
    permission-service/Keycloak, gleiche bestehende Lücke wie überall sonst
    im System). ``None`` je Feld bedeutet "Basis gilt", ein gesetzter Wert
    überschreibt die Basis für Aufrufer mit dieser Rolle. Bei mehreren
    zugewiesenen Rollen mit widersprüchlichen Overrides gewinnt "protokollieren"
    (siehe main._resolve_should_log) - Sicherheits-first, eine Rolle, die mehr
    Protokollierung verlangt, kann nicht durch eine andere Rolle desselben
    Nutzers stillschweigend unterlaufen werden."""

    __tablename__ = "audit_trace_role_override"

    role: Mapped[str] = mapped_column(String(128), primary_key=True)
    log_viewed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    log_downloaded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
