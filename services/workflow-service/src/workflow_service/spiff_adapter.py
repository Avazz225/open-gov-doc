"""Dünner Wrapper um die gesamte SpiffWorkflow-API-Oberfläche (Konzept 7.1, P6-S1).

Isoliert jede Annahme über die (nicht offiziell stabil garantierte) SpiffWorkflow-API
in einer einzigen Datei, damit ein künftiger Versions-Bump nur hier nachgezogen werden
muss statt verstreut in `repository.py`. Gegen die tatsächlich installierte Version
**SpiffWorkflow 3.1.2** (siehe `pyproject.toml`) per `help()`/`inspect` verifiziert,
nicht nur aus der Doku übernommen:

- Parsing: `SpiffWorkflow.bpmn.parser.BpmnParser` - `add_bpmn_str(bytes|str)`,
  `get_process_ids()` (liefert **nur** ausführbare Top-Level-Prozesse, filtert bereits
  intern auf `process_executable`), `get_spec(process_id)`. Seit P6-S7 wird stattdessen
  `SpiffWorkflow.camunda.parser.CamundaParser` verwendet (Unterklasse von `BpmnDmnParser`,
  identische `add_bpmn_str`/`get_process_ids`/`get_spec`-Oberfläche) - mappt `manualTask`
  weiterhin auf `ManualTask` (`.manual is True` unverändert), parst aber zusätzlich
  `bpmn:extensionElements/camunda:properties/camunda:property`-Knoten in
  `task_spec.extensions` (dict, string-wertig). Grundlage für den neuen "Signature Task"
  (3.10): ein BPMN-`manualTask` mit `camunda:properties` `taskType=signature`/
  `requiredLevel=...` bleibt technisch ein gewöhnlicher Manual Task (kein neues BPMN-
  Element, kein Modeler-Tooling-Bruch), ist aber über `extensions` fachlich erkennbar.
- Ausführung: `SpiffWorkflow.bpmn.workflow.BpmnWorkflow(spec)`, `do_engine_steps()`
  (führt alle bereiten nicht-manuellen Tasks - u. a. Script Tasks - automatisch aus und
  hält vor einem Manual/User Task an), `is_completed()`.
- Bereite Tasks: `wf.get_tasks(state=TaskState.READY, manual=True)` liefert sowohl
  Manual- als auch User-Tasks (beide haben `task_spec.manual is True`). `task.id` ist
  ein `uuid.UUID`, stabil über Serialisierung/Deserialisierung hinweg (verifiziert).
  `task.task_spec.lane` ist der Bahn-/Rollenname aus dem BPMN-Modell, `None` falls das
  Modell keine Lanes definiert.
- Task abschließen: `task.set_data(**data)`, `task.run()`, danach erneut
  `do_engine_steps()`, damit der Workflow über den abgeschlossenen Task hinaus läuft.
- Serialisierung: `BpmnWorkflowSerializer(BpmnWorkflowSerializer.configure())`,
  `serializer.serialize_json(wf) -> str`, `serializer.deserialize_json(json_str) -> wf`.
- Timer/Boundary Events (P6-S2, SLA-Zeitüberwachung, 7.1): `wf.refresh_waiting_tasks()`
  überführt fällige `WAITING`-Timer-Tasks nach `READY`; ein anschließendes
  `do_engine_steps()` führt sie aus. Ein gefeuerter Boundary-Timer ist über
  `do_engine_steps(did_complete_task=...)` abfangbar, gefiltert auf
  `isinstance(task.task_spec, BoundaryEvent)` (aus
  `SpiffWorkflow.bpmn.specs.mixins.events.intermediate_event`). Beide BPMN-Semantiken
  real gegen die installierte Version getestet (echte, aus dem offiziellen
  `sartography/SpiffWorkflow`-Repo geladene Fixture `boundary_timer_on_task.bpmn`):
  ein non-interrupting Boundary-Timer (`cancelActivity="false"`) feuert die
  Eskalationsverzweigung, während der ursprüngliche Task bereit bleibt und normal
  abschließbar ist; ein interrupting Boundary-Timer (`cancelActivity="true"`, BPMN-
  Default falls das Attribut fehlt) storniert den ursprünglichen Task und lässt den
  Workflow ausschließlich in die Eskalationsverzweigung laufen - beides vollständig
  SpiffWorkflow-eigene Semantik, dieses Modul muss dafür keine eigene Cancel-/
  Routing-Logik schreiben, nur `refresh_waiting_tasks()`+`do_engine_steps()` aufrufen.
- Connector-Service-Tasks (7.1 "Auslösen eines Connector-Aufrufs", P12-S2): ein
  `bpmn:serviceTask` mit `camunda:properties` `taskType=connector_call`/`serviceUrl=...`
  wird über `OVERRIDE_PARSER_CLASSES` (von `BpmnParser` selbst dokumentierter
  Erweiterungspunkt, "provides a map from full BPMN tag to parser/spec classes") auf
  eine eigene `ConnectorServiceTask`-Spec-Klasse statt SpiffWorkflows Default-`ServiceTask`
  gemappt. `ServiceTask._execute()` ist selbst ein dokumentierter No-Op ("Please override
  for specific Implementations") - `ConnectorServiceTask._execute()` ruft einen modul-weiten,
  injizierbaren Handler auf (`register_connector_task_handler()`), der bei `do_engine_steps()`
  synchron aufgerufen wird (SpiffWorkflow ist durchgehend synchron, kein async/await
  irgendwo in der Engine) und die JSON-Antwort in `task.data` merged. Bewusst generisch
  (nur `serviceUrl`, kein Wissen über den aufrufenden Service) - jeder künftige Service
  kann einen automatischen BPMN-Schritt treiben, ohne dass workflow-service ihn kennen
  muss. Ein `serviceTask` OHNE `taskType=connector_call` bleibt unverändert ein echtes
  No-Op (Rückwärtskompatibilität).
- DMN-1.3-Entscheidungstabellen/Business Rule Task (7.1, P14-S4): `CamundaParser`
  (seit P6-S7 Basisklasse, siehe oben) ist bereits selbst eine Unterklasse von
  `SpiffWorkflow.dmn.parser.BpmnDmnParser` (empirisch per `CamundaParser.__mro__`
  verifiziert) - `businessRuleTask` ist dort bereits über `OVERRIDE_PARSER_CLASSES`
  auf `BusinessRuleTaskParser`/`BusinessRuleTask` gemappt, beide unverändert von
  diesem Modul übernommen (kein eigener Override nötig, anders als bei Service-/
  Connector-Tasks). `parser.add_dmn_str(xml)` lädt eine DMN-Datei in denselben
  Parser, geschlüsselt über deren eigene `<decision id="...">` in
  `parser.dmn_parsers` - `BusinessRuleTaskParser.create_task()` liest
  `camunda:decisionRef` vom `<bpmn:businessRuleTask>`-Element und löst ihn exakt
  gegen diesen Schlüssel auf (`ValidationException`, falls nicht gefunden). Beide
  `add_dmn_str`/`add_bpmn_str` müssen VOR `parser.get_spec()` aufgerufen sein
  (das baut den vollständigen Task-Spec-Baum inkl. aller referenzierten
  Decisions), Reihenfolge zwischen beiden ist egal - `_new_parser()` lädt
  deshalb hier alle übergebenen DMN-Definitionen vor der BPMN-Datei selbst.
  `DMNEngine` (Auswertung zur Laufzeit) wird von SpiffWorkflow selbst
  transparent aus `BusinessRuleTaskMixin._run_hook()` aufgerufen, genau wie bei
  jedem anderen automatischen Task (`do_engine_steps()`) - dieses Modul instanziiert
  es nie direkt. Eine DMN-Datei mit mehr als einer `<decision>` wird von
  SpiffWorkflow selbst abgelehnt ("Multiple decision tables are not currently
  supported") - `parse_dmn()` erzwingt das zusätzlich explizit beim Upload,
  damit die Fehlermeldung nicht erst beim nächsten BPMN-Parse-Versuch auftaucht.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from SpiffWorkflow.bpmn.parser.util import full_tag
from SpiffWorkflow.bpmn.serializer import BpmnWorkflowSerializer
from SpiffWorkflow.bpmn.serializer.default.task_spec import BpmnTaskSpecConverter
from SpiffWorkflow.bpmn.specs.defaults import ServiceTask
from SpiffWorkflow.bpmn.specs.mixins.events.intermediate_event import BoundaryEvent
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.camunda.parser import CamundaParser
from SpiffWorkflow.camunda.parser.task_spec import CamundaTaskParser
from SpiffWorkflow.camunda.serializer.config import CAMUNDA_CONFIG
from SpiffWorkflow.task import Task, TaskState


class BpmnParseError(Exception):
    """Die BPMN-XML ist nicht wohlgeformt, enthält keinen (eindeutigen) ausführbaren
    Prozess, referenziert eine unbekannte DMN-`decisionRef`, oder die gewählte
    `process_id` existiert nicht. Bewusst keine Exception aus `repository.py` -
    dieses Modul kennt dessen Fehlerhierarchie nicht, `repository.py` übersetzt
    beim Aufruf in seine eigene `InvalidBpmnError`."""


class DmnParseError(Exception):
    """Die DMN-XML ist nicht wohlgeformt oder enthält nicht genau eine
    `<decision>` (7.1, P14-S4 - SpiffWorkflow selbst unterstützt nur genau eine
    Decision je DMN-Datei, siehe Moduldocstring)."""


@dataclass
class TaskInfo:
    id: str
    name: str
    lane: str | None
    data: dict[str, Any]
    extensions: dict[str, str] = field(default_factory=dict)


@dataclass
class FiredBoundaryEvent:
    name: str
    lane: str | None
    data: dict[str, Any]


class ConnectorTaskHandler(Protocol):
    def __call__(self, extensions: dict[str, str], data: dict[str, Any]) -> dict[str, Any]: ...


_connector_task_handler: ConnectorTaskHandler | None = None


def register_connector_task_handler(handler: ConnectorTaskHandler | None) -> None:
    """Registriert den Callback, den `ConnectorServiceTask._execute()` bei jedem
    `taskType=connector_call`-Service-Task aufruft (P12-S2). Modul-weiter Callback statt
    Konstruktor-Parameter, da SpiffWorkflows Parser die Spec-Klasse selbst instanziiert
    (`OVERRIDE_PARSER_CLASSES`, kein Hook für zusätzliche Konstruktor-Argumente).
    `None` deregistriert (Tests räumen so zwischen Fällen auf)."""
    global _connector_task_handler
    _connector_task_handler = handler


class ConnectorServiceTask(ServiceTask):
    """`bpmn:serviceTask` mit `camunda:properties` `taskType=connector_call` (P12-S2,
    siehe Moduldocstring) - alle anderen Service-Tasks bleiben unverändert No-Ops."""

    def _execute(self, task: Task) -> bool:
        # `_run_hook()` (Basisklasse `TaskSpec`) wertet den Rückgabewert aus: ein
        # falsy Ergebnis (z. B. `None`, SpiffWorkflows eigener `ServiceTask`-Default)
        # lässt den Task dauerhaft in STARTED hängen statt COMPLETED zu werden - real
        # aufgetreten, bevor dieses `return True` ergänzt wurde. `ScriptTask._execute()`
        # folgt demselben Vertrag (`return task.workflow.script_engine.execute(...)`).
        extensions = dict(getattr(self, "extensions", None) or {})
        if extensions.get("taskType") != "connector_call":
            return True
        if _connector_task_handler is None:
            raise RuntimeError(
                "connector_call Service Task ohne registrierten Handler "
                "(register_connector_task_handler() wurde nicht aufgerufen)"
            )
        result = _connector_task_handler(extensions, dict(task.data))
        task.data.update(result)
        return True


class DmsBpmnParser(CamundaParser):
    """`CamundaParser` + `serviceTask` -> `ConnectorServiceTask` (P12-S2) - alle übrigen
    Overrides (`manualTask`/`userTask`/... für Signature/Federation, siehe ADR 0025/0028)
    bleiben von `CamundaParser` unverändert übernommen."""

    OVERRIDE_PARSER_CLASSES = {
        **CamundaParser.OVERRIDE_PARSER_CLASSES,
        # `CamundaTaskParser`, nicht die Basis-`TaskParser` (SpiffWorkflows eigener
        # Default für `serviceTask`) - nur `CamundaTaskParser` liest
        # `camunda:properties` in `task_spec.extensions` ein (real verifiziert: mit
        # der Basis-`TaskParser` blieb `extensions` immer leer, `taskType` also nie
        # erkennbar).
        full_tag("serviceTask"): (CamundaTaskParser, ConnectorServiceTask),
    }


# `CAMUNDA_CONFIG` statt `BpmnWorkflowSerializer.configure()` - der Wechsel auf
# `CamundaParser` mappt `userTask` auf Camundas eigene `UserTask`-Spec-Klasse statt
# der BPMN-Default-Klasse; ohne den passenden Converter schlägt die JSON-Serialisierung
# bereits bestehender Fixtures mit `userTask`-Elementen (z. B. boundary_timer_on_task.bpmn)
# fehl. `ConnectorServiceTask` ist zusätzlich auf `BpmnTaskSpecConverter` gemappt - der
# generische Converter, den auch `NoneTask`/`ManualTask`/`UserTask` nutzen (SpiffWorkflows
# eigener `ServiceTask` ist in KEINER Default-Konfiguration registriert, "Object of type
# ConnectorServiceTask is not JSON serializable" ohne diesen Eintrag - real aufgetreten).
_SERIALIZER = BpmnWorkflowSerializer(
    BpmnWorkflowSerializer.configure(
        {**CAMUNDA_CONFIG, ConnectorServiceTask: BpmnTaskSpecConverter}
    )
)


def _new_parser(xml: str, dmn_definitions: list[str] | None = None) -> DmsBpmnParser:
    parser = DmsBpmnParser()
    try:
        # DMN vor der BPMN-Datei laden (Reihenfolge ist SpiffWorkflow-seitig egal,
        # siehe Moduldocstring - hier trotzdem zuerst, damit ein fehlerhafter
        # DMN-Eintrag nicht erst nach dem BPMN-Parse-Versuch auffällt). lxml
        # akzeptiert bei einer XML-Encoding-Deklaration (<?xml ... encoding="UTF-8"?>)
        # ausschließlich bytes, keine bereits dekodierten str - deshalb überall
        # explizit kodieren, unabhängig davon, ob der Aufrufer str oder bytes übergibt.
        for dmn_xml in dmn_definitions or []:
            parser.add_dmn_str(dmn_xml.encode("utf-8"))
        parser.add_bpmn_str(xml.encode("utf-8"))
    except Exception as exc:  # SpiffWorkflow/lxml werfen diverse eigene Typen
        raise BpmnParseError(f"BPMN-Datei nicht parsbar: {exc}") from exc
    return parser


def parse_dmn(xml: str) -> str:
    """Validiert eine DMN-1.3-Datei (P14-S4) und liefert ihre interne
    `decision_id` zurück (`<decision id="...">`) - der Schlüssel, über den ein
    `businessRuleTask`s `camunda:decisionRef` sie später referenziert (siehe
    `repository.create_dmn_definition`).

    `BpmnDmnParser.add_dmn_xml()` legt pro Datei immer genau EINEN
    `dmn_parsers`-Eintrag an, geschlüsselt über die ID der ERSTEN `<decision>`
    im Dokument (`DMNParser.bpmn_id`, siehe SpiffWorkflow-Quelltext) - eine
    zweite `<decision>` im selben Dokument bliebe darüber unsichtbar,
    `len(parser.dmn_parsers)` wäre in JEDEM Fall 1 (empirisch verifiziert,
    dieser Weg kann die Mehrfach-Decision-Grenze also NICHT prüfen). Die
    eigentliche `SpiffWorkflow`-eigene Ablehnung ("Multiple decision tables
    are not current supported") passiert erst innerhalb von
    `DMNParser.parse()` - dort aber nur LAZY, ausgelöst durch
    `BpmnDmnParser.get_decision()` beim Auflösen eines referenzierenden
    `businessRuleTask`s (`camunda:decisionRef`), nicht beim Laden selbst.
    Ein Upload OHNE begleitende BPMN-Referenz würde diesen Fehler also nie
    auslösen. `parse_dmn()` ruft `dmn_parser.parse()` deshalb hier explizit
    selbst auf, um die Prüfung schon beim Upload zu erzwingen. Die
    installierte SpiffWorkflow-Version hat dabei einen eigenen Bug: die
    `ValidationException` für den Mehrfach-Fall übergibt eine `list` als
    `node`-Argument, `ValidationException.__init__` erwartet aber ein
    lxml-Element (`node.tag`) und wirft stattdessen einen `AttributeError`
    (empirisch verifiziert) - `except Exception` unten fängt beides ab."""
    parser = DmsBpmnParser()
    try:
        parser.add_dmn_str(xml.encode("utf-8"))
        [dmn_parser] = parser.dmn_parsers.values()
        dmn_parser.parse()
    except Exception as exc:
        raise DmnParseError(f"DMN-Datei nicht parsbar: {exc}") from exc
    return dmn_parser.decision.id


def list_process_ids(xml: str, dmn_definitions: list[str] | None = None) -> list[str]:
    """Ausführbare Top-Level-Prozess-IDs in der BPMN-Datei (zur Auto-Erkennung, wenn
    der Aufrufer keine explizite `process_id` mitgibt)."""
    return _new_parser(xml, dmn_definitions).get_process_ids()


def parse_bpmn(
    xml: str, process_id: str | None, dmn_definitions: list[str] | None = None
) -> tuple[Any, str]:
    """Parst die BPMN-XML und löst die zu instanziierende Prozess-ID auf. Ohne
    `process_id` wird automatisch aufgelöst, aber nur wenn die Datei genau einen
    ausführbaren Top-Level-Prozess enthält - sonst muss der Aufrufer explizit wählen.
    `dmn_definitions` (P14-S4): DMN-XML-Inhalte, die vor der BPMN-Datei in denselben
    Parser geladen werden - jeder von einem `businessRuleTask` referenzierte
    `decisionRef` muss darunter sein, sonst schlägt `get_spec()` unten mit einer
    SpiffWorkflow-eigenen `ValidationException` fehl (übersetzt in `BpmnParseError`)."""
    parser = _new_parser(xml, dmn_definitions)
    available = parser.get_process_ids()
    if process_id is None:
        if len(available) != 1:
            raise BpmnParseError(
                "BPMN-Datei enthält keinen eindeutigen ausführbaren Prozess "
                f"(gefunden: {available}) - process_id muss explizit angegeben werden"
            )
        process_id = available[0]
    try:
        spec = parser.get_spec(process_id)
    except Exception as exc:
        raise BpmnParseError(f"Prozess {process_id!r} nicht auflösbar: {exc}") from exc
    return spec, process_id


def new_workflow(spec: Any) -> BpmnWorkflow:
    return BpmnWorkflow(spec)


def serialize(wf: BpmnWorkflow) -> str:
    return _SERIALIZER.serialize_json(wf)


def deserialize(blob: str) -> BpmnWorkflow:
    return _SERIALIZER.deserialize_json(blob)


def set_initial_data(wf: BpmnWorkflow, data: dict[str, Any]) -> None:
    """Setzt Prozessvariablen vor dem ersten `run_ready_steps()`-Aufruf. `wf.set_data()`
    selbst reicht dafür NICHT (verifiziert) - Task-Daten werden beim Abschluss eines
    Tasks an dessen Kinder weitergereicht, nicht rückwirkend aus dem Workflow-weiten
    `data`-Dict gelesen. Muss daher direkt auf dem/den zu diesem Zeitpunkt bereiten
    Start-Task(s) gesetzt werden, bevor irgendein Task gelaufen ist."""
    if not data:
        return
    for task in wf.get_tasks(state=TaskState.READY):
        task.set_data(**data)


def run_ready_steps(wf: BpmnWorkflow) -> None:
    """Führt alle bereiten automatischen Tasks (Script Tasks etc.) aus und hält vor
    dem nächsten Manual/User Task bzw. beim Abschluss des Workflows an."""
    wf.do_engine_steps()


def retry_errored_tasks(wf: BpmnWorkflow) -> int:
    """Resumability für einen fehlgeschlagenen automatischen Schritt (7.2, P12-S2):
    eine Exception in `TaskSpec._run()` (z. B. `ConnectorServiceTask._execute()`, wenn
    die aufgerufene `serviceUrl` nicht erreichbar war) versetzt den Task nach `ERROR`
    (real verifiziert) - ein einfaches erneutes `do_engine_steps()` reicht NICHT, um ihn
    erneut zu versuchen (SpiffWorkflow verarbeitet dort nur `READY`-Tasks). `reset_branch()`
    (offizielle SpiffWorkflow-API) versetzt einen `ERROR`-Task zurück auf `FUTURE`/`READY`,
    unter Beibehaltung der bisherigen Task-Daten. Gibt die Anzahl zurückgesetzter Tasks
    zurück (0 bedeutet: nichts zum Wiederholen, z. B. bei einer bereits laufenden/
    abgeschlossenen Instanz ohne Fehler)."""
    errored = wf.get_tasks(state=TaskState.ERROR)
    for task in errored:
        task.reset_branch(dict(task.data))
    return len(errored)


def ready_manual_tasks(wf: BpmnWorkflow) -> list[TaskInfo]:
    tasks = wf.get_tasks(state=TaskState.READY, manual=True)
    return [
        TaskInfo(
            id=str(task.id),
            name=task.task_spec.bpmn_name or task.task_spec.name,
            lane=getattr(task.task_spec, "lane", None),
            data=dict(task.data),
            extensions=dict(getattr(task.task_spec, "extensions", None) or {}),
        )
        for task in tasks
    ]


def find_ready_task(wf: BpmnWorkflow, task_id: str) -> Task | None:
    for task in wf.get_tasks(state=TaskState.READY, manual=True):
        if str(task.id) == task_id:
            return task
    return None


def complete_task(task: Task, data: dict[str, Any]) -> None:
    if data:
        task.set_data(**data)
    task.run()


def is_completed(wf: BpmnWorkflow) -> bool:
    return wf.is_completed()


def check_timers(wf: BpmnWorkflow) -> list[FiredBoundaryEvent]:
    """Bringt fällige Timer/Boundary Events zum Feuern (SLA-Zeitüberwachung, P6-S2)
    und meldet zurück, welche Boundary-Timer dabei tatsächlich ausgelöst haben - unabhängig
    davon, ob sie den ursprünglichen Task stornieren (interrupting) oder nicht
    (non-interrupting, siehe Modul-Docstring). Der Datenschnappschuss (`.data`) enthält
    insbesondere `initial_data`-Prozessvariablen wie `escalation_email`, die normal per
    BPMN-Datenfluss bis zum Boundary-Task durchgereicht wurden."""
    fired: list[FiredBoundaryEvent] = []

    def did_complete(task: Task) -> None:
        if isinstance(task.task_spec, BoundaryEvent):
            fired.append(
                FiredBoundaryEvent(
                    name=task.task_spec.bpmn_name or task.task_spec.name,
                    lane=getattr(task.task_spec, "lane", None),
                    data=dict(task.data),
                )
            )

    wf.refresh_waiting_tasks()
    wf.do_engine_steps(did_complete_task=did_complete)
    return fired
