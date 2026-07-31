"""Dünner Wrapper um die gesamte SpiffWorkflow-API-Oberfläche (Konzept 7.1, P6-S1).

Isoliert jede Annahme über die (nicht offiziell stabil garantierte) SpiffWorkflow-API
in einer einzigen Datei, damit ein künftiger Versions-Bump nur hier nachgezogen werden
muss statt verstreut in `repository.py`. Gegen die tatsächlich installierte Version
**SpiffWorkflow 3.1.2** (siehe `pyproject.toml`) per `help()`/`inspect` verifiziert,
nicht nur aus der Doku übernommen:

- Parsing: `SpiffWorkflow.bpmn.parser.BpmnParser` - `add_bpmn_str(bytes|str)`,
  `get_process_ids()` (liefert **nur** ausführbare Top-Level-Prozesse, filtert bereits
  intern auf `process_executable`), `get_spec(process_id)`.
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
"""

from dataclasses import dataclass
from typing import Any

from SpiffWorkflow.bpmn.parser import BpmnParser
from SpiffWorkflow.bpmn.serializer import BpmnWorkflowSerializer
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.task import Task, TaskState

_SERIALIZER = BpmnWorkflowSerializer(BpmnWorkflowSerializer.configure())


class BpmnParseError(Exception):
    """Die BPMN-XML ist nicht wohlgeformt, enthält keinen (eindeutigen) ausführbaren
    Prozess, oder die gewählte `process_id` existiert nicht. Bewusst keine Exception aus
    `repository.py` - dieses Modul kennt dessen Fehlerhierarchie nicht, `repository.py`
    übersetzt beim Aufruf in seine eigene `InvalidBpmnError`."""


@dataclass
class TaskInfo:
    id: str
    name: str
    lane: str | None
    data: dict[str, Any]


def _new_parser(xml: str) -> BpmnParser:
    parser = BpmnParser()
    try:
        # lxml akzeptiert bei einer XML-Encoding-Deklaration (<?xml ... encoding="UTF-8"?>)
        # ausschließlich bytes, keine bereits dekodierten str - deshalb hier explizit
        # kodieren, unabhängig davon, ob der Aufrufer str oder bytes übergibt.
        parser.add_bpmn_str(xml.encode("utf-8"))
    except Exception as exc:  # SpiffWorkflow/lxml werfen diverse eigene Typen
        raise BpmnParseError(f"BPMN-Datei nicht parsbar: {exc}") from exc
    return parser


def list_process_ids(xml: str) -> list[str]:
    """Ausführbare Top-Level-Prozess-IDs in der BPMN-Datei (zur Auto-Erkennung, wenn
    der Aufrufer keine explizite `process_id` mitgibt)."""
    return _new_parser(xml).get_process_ids()


def parse_bpmn(xml: str, process_id: str | None) -> tuple[Any, str]:
    """Parst die BPMN-XML und löst die zu instanziierende Prozess-ID auf. Ohne
    `process_id` wird automatisch aufgelöst, aber nur wenn die Datei genau einen
    ausführbaren Top-Level-Prozess enthält - sonst muss der Aufrufer explizit wählen."""
    parser = _new_parser(xml)
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


def ready_manual_tasks(wf: BpmnWorkflow) -> list[TaskInfo]:
    tasks = wf.get_tasks(state=TaskState.READY, manual=True)
    return [
        TaskInfo(
            id=str(task.id),
            name=task.task_spec.bpmn_name or task.task_spec.name,
            lane=getattr(task.task_spec, "lane", None),
            data=dict(task.data),
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
