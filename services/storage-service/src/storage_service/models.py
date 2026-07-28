from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("storage")


class ObjectMetadata(Base):
    """Metadaten je gespeichertem Objekt - der eigentliche Inhalt liegt nie
    in der Shared DB, nur Referenz, Prüfsumme und Größe (Konzept 3.6)."""

    __tablename__ = "object_metadata"

    object_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    # Primärziel-`id` zum Zeitpunkt der Anlage/letzten Überschreibung (3.6,
    # kein FK auf ein Backend-Verzeichnis - Ziele sind reine Settings, keine
    # eigene Tabelle). Seit P5b-S6 eine Ziel-`id` statt eines Backend-*Typs*
    # (mehrere Instanzen desselben Typs sind jetzt möglich, ADR 0017).
    backend: Mapped[str] = mapped_column(String(64))
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObjectCopy(Base):
    """Je Objekt eine Zeile pro konfiguriertem Redundanz-Ziel (3.6) - Grundlage
    für Lesezugriffs-Fallback, Fixity-Checks je Kopie und die Retry-Queue bei
    asynchroner Replikation. ``status``: ``pending`` (noch nicht repliziert),
    ``ok``, ``failed`` (nächster process-pending-Lauf versucht es erneut) oder
    ``failed_permanent`` (max_replication_attempts erreicht, siehe Settings)."""

    __tablename__ = "object_copy"

    object_key: Mapped[str] = mapped_column(
        String(1024), ForeignKey("storage.object_metadata.object_key"), primary_key=True
    )
    backend_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BackendIdentity(Base):
    """Zuletzt bekannte Geräte-ID je konfiguriertem Ziel (3.6 "Datenträger-
    Wechsel-Sensibilität", P5b-S6). Bewusst eine eigene Tabelle statt sich
    allein auf die vom Backend selbst gelesene Identitätsdatei zu verlassen:
    Ein ausgetauschtes/zurückgesetztes Ziel hat womöglich gar keine
    Identitätsdatei mehr - der Vergleichswert muss deshalb unabhängig vom
    Ziel selbst gespeichert sein, siehe ADR 0017."""

    __tablename__ = "backend_identity"

    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GuardConfig(Base):
    """Admin-UI-editierbare Laufzeit-Konfiguration des Redundanz-Wächters
    (3.6, P5b-S6) - einzelne Zeile mit fester `id=1`, gleiches Muster wie
    `OcrConfig` (ocr-service, P5b-S5/ADR 0016). Bewusst eine **proaktiv**
    gesetzte Standing-Policy ("falls je ein Datenträger-Wechsel erkannt
    wird, degradierten Start erlauben"), nicht ein Notfall-Schalter im
    Moment eines bereits verweigerten Starts - der Service, der die
    Freigabe entgegennehmen müsste, liefe in diesem Moment ja gerade nicht
    (siehe ADR 0017 für die Begründung, warum das trotzdem funktioniert:
    Postgres selbst ist von einem defekten Storage-Backend unabhängig)."""

    __tablename__ = "guard_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    allow_degraded_start: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
