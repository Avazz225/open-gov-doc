from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("workflow")


class ProcessDefinition(Base):
    """Eine importierte BPMN-2.0-Prozessdefinition (2.2/7.1, P6-S1). Import
    per BPMN-XML-Upload, seit P6-S8 auch aus dem eigenständigen Process
    Designer (`apps/process-designer`). ``name`` ist seit P6-S8 der
    Prozessfamilien-Schlüssel, nicht mehr global eindeutig - Eindeutigkeit
    gilt jetzt für ``(name, version)``: ein erneuter Upload unter demselben
    ``name`` legt automatisch die nächste Version an (siehe
    `repository.create_process_definition`), statt mit 409 abgelehnt zu
    werden. Eine Prozessinstanz bindet sich über ``process_definition_id``
    immer an eine konkrete, unveränderliche Version - spätere neue Versionen
    derselben Familie wirken sich nie rückwirkend auf bereits laufende
    Instanzen aus."""

    __tablename__ = "process_definition"
    __table_args__ = (
        UniqueConstraint("name", "version", name="ux_process_definition_name_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Die interne Prozess-ID aus der BPMN-XML selbst (`<bpmn:process id="...">"`),
    # nicht der hier vergebene Anzeigename - wird für jeden `spiff_adapter.parse_bpmn`-
    # Aufruf beim Instanzstart erneut gebraucht.
    bpmn_process_id: Mapped[str] = mapped_column(String(256))
    bpmn_xml: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProcessInstance(Base):
    """Eine laufende oder abgeschlossene Ausführung einer Prozessdefinition.

    ``workflow_state`` ist der vollständige, von ``spiff_adapter.serialize()``
    erzeugte JSON-Blob (ADR 0019: kein separates, normalisiertes Task-Modell -
    SpiffWorkflow besitzt bereits die korrekte BPMN-Ausführungssemantik,
    dieses Modul behandelt den Blob als intransparent und rührt ihn nie
    direkt an, nur über ``spiff_adapter``).

    ``business_key`` ist wie ``folder_id``/``object_type_id`` bei anderen
    Services eine opake Cross-Service-Referenz (z. B. eine künftige
    ``document_id``) ohne FK-Erzwingung über Service-Grenzen hinweg - anders
    als dort wird sie in P6-S1 noch von keinem Aufrufer tatsächlich gegen
    einen anderen Service geprüft."""

    __tablename__ = "process_instance"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    process_definition_id: Mapped[int] = mapped_column(
        ForeignKey("workflow.process_definition.id"), index=True
    )
    business_key: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16))  # "running" | "completed"
    workflow_state: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
