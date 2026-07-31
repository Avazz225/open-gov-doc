from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
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
