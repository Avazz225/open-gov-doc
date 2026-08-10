from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
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


class DmnDefinition(Base):
    """Eine importierte DMN-1.3-Entscheidungstabelle (7.1, P14-S4). Gleiches
    Versionierungsmuster wie ``ProcessDefinition`` (ADR 0027): ``name`` ist der
    Familienschlüssel, ein erneuter Upload unter demselben Namen legt
    automatisch die nächste Version an.

    ``decision_id`` ist die interne ``<decision id="...">`` aus der DMN-XML
    selbst (per ``spiff_adapter.parse_dmn`` extrahiert) - das ist der
    tatsächliche Schlüssel, über den ein ``bpmn:businessRuleTask``s
    ``camunda:decisionRef`` sie referenziert, nicht ``name`` (siehe
    `spiff_adapter.py`). Muss unter den jeweils NEUESTEN Versionen aller
    Familien eindeutig sein (`repository.create_dmn_definition`) - SpiffWorkflow
    lädt für jeden BPMN-Parse immer nur die neueste Version jeder Familie
    in denselben Parser (siehe `repository.list_latest_dmn_xml`), zwei
    Familien mit kollidierender ``decision_id`` wären darin nicht mehr
    unterscheidbar."""

    __tablename__ = "dmn_definition"
    __table_args__ = (UniqueConstraint("name", "version", name="ux_dmn_definition_name_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer, default=1)
    decision_id: Mapped[str] = mapped_column(String(256))
    dmn_xml: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BusinessCalendar(Base):
    """Regionaler Geschäftskalender für die SLA-Fristberechnung (7.1, P14-S5) -
    eine benannte, frei pflegbare Liste arbeitsfreier Tage (Feiertage), die eine
    Timer-Dauerangabe in einer BPMN-Datei per neuer `business_days(n,
    calendar_name)`-Funktion referenzieren kann (siehe `spiff_adapter.py`).
    Wochenenden (Sa/So) gelten dabei IMMER als arbeitsfrei, unabhängig von
    einem konkreten Kalender - `non_working_dates` enthält nur die
    ZUSÄTZLICHEN, kalenderspezifischen Tage (Feiertage). Anders als
    `ProcessDefinition`/`DmnDefinition` bewusst KEIN Versionierungsmuster:
    ein Kalender ist fortlaufend zu pflegende Referenzdaten (z. B. jährlich
    neue Feiertage nachtragen), keine unveränderliche, versionierte
    Ausführungslogik - normales Update-in-Place wie bei `SensorConfig`/
    `ApprovalConfig`.

    `is_default`: höchstens EIN Kalender darf gleichzeitig `True` sein
    (`repository.py` setzt beim Setzen eines neuen Default-Kalenders alle
    anderen zurück) - wird von `business_days(n)` OHNE explizites
    `calendar_name`-Argument verwendet (Installationsweiter Default, siehe
    Konzept 7.1: "einem Prozess/einer Installation zuordenbar" - ein explizit
    benannter Kalender in der BPMN-Datei selbst ist die Prozess-Zuordnung,
    dieser Default die Installations-Zuordnung)."""

    __tablename__ = "business_calendar"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    non_working_dates: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
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


class FederationIdentity(Base):
    """Eigene Federation-Identität dieser Installation (7.4, P6-S9) - bewusst
    eine einzelne Zeile mit fester ``id=1``, gleiches Singleton-Muster wie
    `signature-service`s `InternalCa`. Wird beim ersten Start mit
    konfiguriertem `Settings.federation_hub_base_url` erzeugt (eigenes
    RSA-2048-Schlüsselpaar, siehe `federation_crypto.py`) und danach
    idempotent wiederverwendet - andere Installationen verschlüsseln Payloads
    für uns mit `public_key_pem`, `private_key_pem` entschlüsselt sie wieder.
    Seit P13-S4 (ADR 0039) dient dasselbe Schlüsselpaar zusätzlich als
    kryptografische Identität gegenüber dem Hub: jeder eigene Aufruf wird mit
    `private_key_pem` signiert (`X-Installation-Signature`), statt - wie
    zuvor - einen vom Hub ausgegebenen `api_key` als Bearer-Token
    mitzuschicken (das Feld entfällt daher). `hub_public_key_pem` ist der
    einmalig abgerufene öffentliche Schlüssel des Hub (Trust-on-First-Use,
    ADR 0028), mit dem eingehende, vom Hub signierte Zustellungen verifiziert
    werden."""

    __tablename__ = "federation_identity"

    id: Mapped[int] = mapped_column(primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(128))
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    hub_public_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FederationConfig(Base):
    """Versionskompatibilitäts-Erklärung dieser Installation (7.4, P13-S3) -
    bewusst eine eigene, per `config-service`/7.3 ex-/importierbare Zeile
    statt eines reinen `Settings`-Felds: 7.4 verlangt wörtlich, dass diese
    Spanne "Teil des ohnehin schon versionierten Konfigurationsschemas (7.3)"
    ist und "mit jedem Release explizit gepflegt wird, nicht implizit
    angenommen" - vor P13-S3 lebte sie ausschließlich in
    `Settings.installation_version`/`installation_min_compatible_peer_version`
    und war damit nur über einen Container-Neustart änderbar, nie über den
    regulären Konfigurationsimport erreichbar. Singleton-Zeile (``id=1``,
    gleiches Muster wie `FederationIdentity`), bei erstem Zugriff aus den
    `Settings`-Defaults geseedet (rückwärtskompatibel)."""

    __tablename__ = "federation_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32))
    min_compatible_peer_version: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FederationTask(Base):
    """Bindeglied zwischen einem lokalen Manual-Task/einer lokalen Instanz und
    einem beim Hub laufenden Handover (7.4, P6-S9) - verhindert doppeltes
    Dispatchen desselben `federated`/`federated_return`-Tasks (siehe
    `main.py._dispatch_pending_federation_tasks`) und hält bei einer
    eingehenden (`direction="inbound"`) Übergabe fest, an welche
    `origin_installation_id`/welchen `handover_id` ein späterer
    `federated_return`-Task sein Ergebnis zurückschicken muss. ``task_id`` ist
    bei einer rein eingehenden Zeile (die neue Instanz selbst, bevor sie einen
    `federated_return`-Task erreicht hat) noch ``None``.

    Eindeutigkeit gilt für ``(handover_id, direction)``, nicht ``handover_id``
    allein: im Normalfall sieht eine einzelne Installation einen `handover_id`
    ohnehin nur aus genau einer Richtung (entweder sie hat ihn selbst erzeugt,
    oder sie empfängt ihn) - **außer** beim in dieser Session verwendeten
    Selbst-Loopback-Smoke-Test (eine Installation übergibt an sich selbst),
    wo derselbe `handover_id` in derselben Datenbank sowohl als `outbound`-
    als auch als `inbound`-Zeile auftritt."""

    __tablename__ = "federation_task"
    __table_args__ = (
        UniqueConstraint("handover_id", "direction", name="ux_federation_task_handover_direction"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    process_instance_id: Mapped[str] = mapped_column(
        ForeignKey("workflow.process_instance.id"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handover_id: Mapped[str] = mapped_column(String(36), index=True)
    direction: Mapped[str] = mapped_column(String(16))  # "outbound" | "inbound"
    origin_installation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
