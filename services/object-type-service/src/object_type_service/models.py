from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("object_type")


class ObjectType(Base):
    """Objekttyp-Definition (2.2): Attribute, Namenskonventionen, bedingte
    Regeln. Angewandt wird das Schema nicht hier, sondern über
    ``dms-constraint-engine`` im ``/validate``-Endpunkt (siehe main.py)."""

    __tablename__ = "object_type"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    applies_to: Mapped[str] = mapped_column(String(16))  # "document" | "folder"
    attributes: Mapped[list] = mapped_column(JSON, default=list)
    naming_constraints: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    # Erzwungene Objekt-Hierarchie (2.2a): Namen zulässiger Eltern-Ordnerklassen,
    # oder der Sentinel "$ROOT" für Platzierung direkt unter der Wurzel. None/leer
    # = überall platzierbar (Rückwärtskompatibilität zu Typen ohne diese Angabe).
    allowed_parent_types: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Nur für Ordnerklassen (applies_to == "folder") gesetzt (2.2a) - Anzeige im
    # User-UI-Explorer vor dem Namen folgt erst mit P5b-S4.
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Kennzeichengenerator (2.2, seit P5e-S1) - nur für applies_to == "document"
    # gesetzt. None = kein Generator konfiguriert. Format-String mit
    # Platzhaltern {YYYY}/{YY}/{MM}/{DD}/{Laufende_Nummer}, siehe
    # repository._render_kennzeichen.
    kennzeichen_format: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Tri-State-Override (None = kein Override, es gilt der globale Standard
    # aus P5e-S2) des globalen "Kennzeichen vor Dateinamen anzeigen"-Schalters.
    kennzeichen_display_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Mindest-Signaturniveau (3.10, seit P6-S7) - nur für applies_to == "document"
    # gesetzt (Ordner werden nicht signiert). None = keine Anforderung. Wird vom
    # Signature Service bei jedem Signiervorgang gegen das angeforderte Niveau
    # geprüft (`ses` < `aes` < `qes`), siehe signature-service/main.py.
    required_signature_level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Aufbewahrung (5.2, seit P7-S1): Standard-Aufbewahrungsfrist in Tagen ab
    # Anlage, für applies_to == "document" UND "folder" gleichermaßen gültig
    # (gilt für beide Session P7-S1/P7-S1b). None = kein Typ-Standard, Frist
    # bleibt manuell zu setzen.
    default_retention_days: Mapped[int | None] = mapped_column(nullable=True)
    # Tri-State-Override (None = kein Override, es gilt der globale Standard
    # aus RetentionConfig) - ob ein Löschgrund bei Zwangslöschung Pflicht ist
    # (5.2a), gleiches Muster wie kennzeichen_display_override.
    deletion_reason_required_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Aussonderung (5.6, seit P7-S3): Standard-Frist in Tagen ab Anlage, nach
    # deren Ablauf ein Dokument aussonderungsfaehig wird (archival-service
    # loest daraus einmalig Document.archive_after auf) - exakt gleiches
    # Muster wie default_retention_days, aber unabhaengig davon (Aussonderung
    # ist laut Konzept ergaenzend zur regulaeren Aufbewahrungsfrist, kein
    # dritter Zweig derselben Entscheidung). None = kein Typ-Standard, nur
    # manueller Trigger moeglich.
    default_archive_after_days: Mapped[int | None] = mapped_column(nullable=True)
    # Ob die Archivkopie vor der Ablage im Archiv-Ziel verschluesselt wird
    # (5.6) - ueber archival-service's KeyStore-Plugin-Schnittstelle (ADR
    # 0029), Default aus false (unverschluesselt).
    archive_encryption_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Verschlusssachen-Einstufung (2.5, P15-S1, seit P17-S2 mehrstufig statt
    # binär - 14.2) - nur für applies_to == "document" gesetzt (der
    # Konzepttext nennt ausdrücklich nur Dokumente, keine Ordner). None =
    # nicht eingestuft, sonst eine der vier gängigen deutschen
    # VS-Einstufungen (siehe schemas.ClassificationLevel). Jeder gesetzte
    # Wert (unabhängig von der konkreten Stufe) löst weiterhin denselben
    # binären Gate wie zuvor aus: ein klassifiziertes, gelöschtes Dokument
    # landet im strukturell getrennten Verschlusssachen-Papierkorb statt im
    # regulären, siehe document-service `settings.classified_trash_hard_
    # delete_admin_role` - die konkrete Stufe ist reine Zusatzinformation
    # (Anzeige/Audit), keine zusätzliche Rollen-Differenzierung in dieser
    # Referenzimplementierung. Ersetzt das bis P17-S1 rein binäre `is_
    # classified: bool` (identisches schema-gebundenes Architekturmuster,
    # siehe ADR 0051 "Begründung" - nur der Werteraum wurde erweitert).
    classification_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObjectTypeSequence(Base):
    """Atomarer, gegen Nebenläufigkeit abgesicherter Jahres-Zähler für den
    Kennzeichengenerator (P5e-S1): eine Zeile je Objekttyp und Jahr, damit der
    Zähler zum Jahreswechsel automatisch wieder bei 1 beginnt (Nutzerentscheidung
    "Reset pro Jahr", siehe PROGRESS.md)."""

    __tablename__ = "object_type_sequence"

    object_type_id: Mapped[int] = mapped_column(
        ForeignKey("object_type.object_type.id", ondelete="CASCADE"), primary_key=True
    )
    jahr: Mapped[int] = mapped_column(primary_key=True)
    naechste_nummer: Mapped[int] = mapped_column(default=1)


class KennzeichenConfig(Base):
    """Globaler Anzeige-Standard für den Kennzeichengenerator (2.2, seit
    P5e-S3) - bewusst eine einzelne Zeile mit fester ``id=1``, gleiches Muster
    wie ``OcrConfig``/``UploadConfig`` der anderen Services (eine Installation,
    keine Mandantentrennung, 3a). Einzelne Dokumentenarten können diesen
    Standard über ``ObjectType.kennzeichen_display_override`` überschreiben."""

    __tablename__ = "kennzeichen_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    show_before_filename: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ObjectTypeLayout(Base):
    """Formular-Layout je Objekttyp und Verwendungszweck (2.2b, seit P5b-S2):
    ``purpose`` ist ``"display"``|``"search"``|``"upload"``. Nur explizit vom
    generierten "Smart Layout" abweichende Layouts werden hier persistiert
    (siehe ``object_type_service.layout.generate_smart_layout``) - ohne eigene
    Zeile wird bei jedem Lesezugriff aus der aktuellen Attributliste neu
    generiert, damit ein unveränderter Default nie veraltet."""

    __tablename__ = "object_type_layout"

    object_type_id: Mapped[int] = mapped_column(
        ForeignKey("object_type.object_type.id", ondelete="CASCADE"), primary_key=True
    )
    purpose: Mapped[str] = mapped_column(String(16), primary_key=True)
    layout: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
