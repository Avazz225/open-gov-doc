from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

# Schema-Name "case" ist ein reserviertes SQL-Schlüsselwort (CASE WHEN) -
# SQLAlchemy quotet ihn in generierten DDL-Statements automatisch (verifiziert
# gegen PGIdentifierPreparer), rohe SQL-Strings in main.py/tests/conftest.py
# müssen ihn dagegen selbst als `"case"` quoten. Tabellenname bewusst "cases"
# (Plural) statt "case", um diese Quoting-Pflicht nicht auch dort zu brauchen.
Base = make_declarative_base("case")


class Case(Base):
    """Umlaufmappe (2.3): eigenständiges, objekttypfähiges Objekt mit eigenem
    Lebenszyklus über eine workflow-service-Prozessinstanz (P6-S1).
    `process_instance_id`s `business_key` ist bewusst identisch mit `id`
    (kein separates Feld nötig) - Grundlage dafür, wie `consumer.py` den
    Abschluss der zugehörigen Prozessinstanz einer Umlaufmappe zuordnet."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    object_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    # "open" waehrend der Bearbeitung, "closed" nach Erreichen des
    # BPMN-Endzustands (Abschluss-Snapshot, siehe close_case in repository.py).
    status: Mapped[str] = mapped_column(String(32), default="open")
    process_definition_id: Mapped[int] = mapped_column(Integer)
    process_instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Aussonderung (5.6, seit P7-S3b) - `archive_after` wird erst bei
    # Abschluss aufgeloest (siehe repository.close_case), nicht bei Anlage
    # wie `Document.archive_after`, da nur geschlossene Umlaufmappen
    # aussonderungsfaehig sind.
    archive_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Vorgangsnummer (2.3/2.5, P15-S3): server-generierter, installationsweit
    # eindeutiger Bezug (anders als `Document.attributes["Kennzeichen"]", das
    # nur je Objekttyp+Jahr eindeutig ist) - Grundlage für den neuen
    # `mail-connector`, der eingehende Post anhand eines im Betreff/Text
    # gefundenen Tokens automatisch einer Umlaufmappe zuordnen können muss.
    # Nullable, da nur für ab dieser Session neu angelegte Fälle vergeben
    # (kein rückwirkendes Befüllen bestehender Zeilen). Bewusst NICHT über
    # PATCH änderbar (anders als Kennzeichen) - ein stabiler, rein
    # systemseitig vergebener Bezug ist Voraussetzung für verlässliches
    # Matching.
    vorgangsnummer: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CaseDocumentReference(Base):
    """Referenz einer Umlaufmappe auf ein Dokument (2.3) - waehrend der
    Bearbeitung dynamisch (Version wird bei jedem Lesezugriff live aus dem
    Document Service aufgeloest, siehe repository.list_document_references),
    ab Abschluss der Umlaufmappe fixiert in `snapshot_version_number`
    (Abschluss-Snapshot). Entfernte Referenzen werden weich geloescht
    (`removed_by`/`removed_at`) statt hart entfernt - Nachvollziehbarkeit."""

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
    """Installationsweite Aussonderungs-Konfiguration (5.6, seit P7-S3b) -
    einzelne Zeile (`id=1`, gleiches Muster wie document-services
    `RetentionConfig`). Kein Pendant zu `ObjectType.default_archive_after_
    days`/`archive_encryption_enabled`: `ObjectType.applies_to` kennt nur
    `"document"`/`"folder"`, keine eigene Kategorie fuer Umlaufmappen - siehe
    docs/services/archival-service.md."""

    __tablename__ = "case_archival_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    default_archive_after_days_closed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    archive_encryption_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseNumberConfig(Base):
    """Installationsweiter Format-String für die Vorgangsnummer (2.3/2.5,
    P15-S3) - einzelne Zeile (`id=1`), gleiches Muster wie
    `CaseArchivalConfig`. Bewusst EIN installationsweites Schema statt eines
    je-Objekttyp-Generators wie beim Kennzeichen (object-type-service kennt
    "case" nicht als eigene `applies_to`-Kategorie, siehe Kommentar an
    `CaseArchivalConfig` oben) - für den hier verfolgten Zweck (verlässliches
    Matching eingehender Post) reicht ein einzelner, garantiert eindeutiger
    Zähler."""

    __tablename__ = "case_number_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    format: Mapped[str] = mapped_column(String(64), default="{YYYY}-{Laufende_Nummer}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseSequence(Base):
    """Atomarer, gegen Nebenläufigkeit abgesicherter Jahres-Zähler für die
    Vorgangsnummer (P15-S3) - gleiches Idiom wie object-type-services
    `ObjectTypeSequence` (P5e-S1), hier ohne Objekttyp-Dimension (eine Zeile
    je Jahr statt je Objekttyp+Jahr)."""

    __tablename__ = "case_sequence"

    jahr: Mapped[int] = mapped_column(primary_key=True)
    naechste_nummer: Mapped[int] = mapped_column(default=1)
