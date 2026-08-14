"""Thin wrapper around the entire SpiffWorkflow API surface (Concept 7.1, P6-S1).

Isolates every assumption about the (not officially stably guaranteed) SpiffWorkflow
API in a single file, so a future version bump only needs to be applied here instead
of scattered across `repository.py`. Verified against the actually installed version
**SpiffWorkflow 3.1.2** (see `pyproject.toml`) via `help()`/`inspect`, not just taken
from the docs:

- Parsing: `SpiffWorkflow.bpmn.parser.BpmnParser` - `add_bpmn_str(bytes|str)`,
  `get_process_ids()` (returns **only** executable top-level processes, already
  filters internally on `process_executable`), `get_spec(process_id)`. Since P6-S7,
  `SpiffWorkflow.camunda.parser.CamundaParser` is used instead (subclass of
  `BpmnDmnParser`, identical `add_bpmn_str`/`get_process_ids`/`get_spec` surface) -
  still maps `manualTask` to `ManualTask` (`.manual is True` unchanged), but
  additionally parses `bpmn:extensionElements/camunda:properties/camunda:property`
  nodes into `task_spec.extensions` (dict, string-valued). Basis for the new
  "Signature Task" (3.10): a BPMN `manualTask` with `camunda:properties`
  `taskType=signature`/`requiredLevel=...` technically remains an ordinary Manual
  Task (no new BPMN element, no modeler tooling break), but is functionally
  recognizable via `extensions`.
- Execution: `SpiffWorkflow.bpmn.workflow.BpmnWorkflow(spec)`, `do_engine_steps()`
  (automatically executes all ready non-manual tasks - including Script Tasks -
  and stops before a Manual/User Task), `is_completed()`.
- Ready tasks: `wf.get_tasks(state=TaskState.READY, manual=True)` returns both
  Manual and User Tasks (both have `task_spec.manual is True`). `task.id` is a
  `uuid.UUID`, stable across serialization/deserialization (verified).
  `task.task_spec.lane` is the lane/role name from the BPMN model, `None` if the
  model defines no lanes.
- Completing a task: `task.set_data(**data)`, `task.run()`, then `do_engine_steps()`
  again so the workflow proceeds past the completed task.
- Serialization: `BpmnWorkflowSerializer(BpmnWorkflowSerializer.configure())`,
  `serializer.serialize_json(wf) -> str`, `serializer.deserialize_json(json_str) -> wf`.
- Timer/boundary events (P6-S2, SLA time monitoring, 7.1): `wf.refresh_waiting_tasks()`
  moves due `WAITING` timer tasks to `READY`; a subsequent `do_engine_steps()` then
  executes them. A fired boundary timer can be caught via
  `do_engine_steps(did_complete_task=...)`, filtered on
  `isinstance(task.task_spec, BoundaryEvent)` (from
  `SpiffWorkflow.bpmn.specs.mixins.events.intermediate_event`). Both BPMN semantics
  were tested for real against the installed version (a real fixture,
  `boundary_timer_on_task.bpmn`, loaded from the official `sartography/SpiffWorkflow`
  repo): a non-interrupting boundary timer (`cancelActivity="false"`) fires the
  escalation branch while the original task stays ready and can complete normally;
  an interrupting boundary timer (`cancelActivity="true"`, BPMN default if the
  attribute is missing) cancels the original task and drives the workflow
  exclusively into the escalation branch - both entirely SpiffWorkflow-native
  semantics, this module needs no own cancel/routing logic for it, only calling
  `refresh_waiting_tasks()`+`do_engine_steps()`.
- Connector service tasks (7.1 "triggering a connector call", P12-S2): a
  `bpmn:serviceTask` with `camunda:properties` `taskType=connector_call`/`serviceUrl=...`
  is mapped, via `OVERRIDE_PARSER_CLASSES` (an extension point documented by
  `BpmnParser` itself, "provides a map from full BPMN tag to parser/spec classes"),
  onto our own `ConnectorServiceTask` spec class instead of SpiffWorkflow's default
  `ServiceTask`. `ServiceTask._execute()` is itself a documented no-op ("Please
  override for specific Implementations") - `ConnectorServiceTask._execute()` calls
  a module-wide, injectable handler (`register_connector_task_handler()`), which is
  called synchronously during `do_engine_steps()` (SpiffWorkflow is entirely
  synchronous, no async/await anywhere in the engine) and merges the JSON response
  into `task.data`. Deliberately generic (only `serviceUrl`, no knowledge of the
  calling service) - any future service can drive an automatic BPMN step without
  workflow-service needing to know it. A `serviceTask` WITHOUT
  `taskType=connector_call` remains an unchanged, genuine no-op (backward
  compatibility).
- DMN 1.3 decision tables/Business Rule Task (7.1, P14-S4): `CamundaParser`
  (base class since P6-S7, see above) is itself already a subclass of
  `SpiffWorkflow.dmn.parser.BpmnDmnParser` (empirically verified via
  `CamundaParser.__mro__`) - `businessRuleTask` is already mapped there via
  `OVERRIDE_PARSER_CLASSES` onto `BusinessRuleTaskParser`/`BusinessRuleTask`, both
  taken over unchanged by this module (no own override needed, unlike for
  service/connector tasks). `parser.add_dmn_str(xml)` loads a DMN file into the
  same parser, keyed by its own `<decision id="...">` in `parser.dmn_parsers` -
  `BusinessRuleTaskParser.create_task()` reads `camunda:decisionRef` from the
  `<bpmn:businessRuleTask>` element and resolves it exactly against this key
  (`ValidationException` if not found). Both `add_dmn_str`/`add_bpmn_str` must be
  called BEFORE `parser.get_spec()` (which builds the complete task spec tree
  including all referenced decisions); the order between the two does not matter -
  `_new_parser()` therefore loads all supplied DMN definitions here before the
  BPMN file itself. `DMNEngine` (runtime evaluation) is called transparently by
  SpiffWorkflow itself from `BusinessRuleTaskMixin._run_hook()`, exactly like any
  other automatic task (`do_engine_steps()`) - this module never instantiates it
  directly. A DMN file with more than one `<decision>` is rejected by SpiffWorkflow
  itself ("Multiple decision tables are not currently supported") - `parse_dmn()`
  additionally enforces this explicitly on upload, so the error message doesn't
  first surface at the next BPMN parse attempt.
- Regional business calendars for SLA deadline calculation (7.1, P14-S5):
  SpiffWorkflow offers no built-in facility for this (already verified in
  P14-S0). `DurationTimerEventDefinition.has_fired()` (installed version, read
  via `inspect`) evaluates the timer duration exactly ONCE - on the very first
  `has_fired()` call, as soon as the associated timer task becomes `WAITING` -
  via `my_task.workflow.script_engine.evaluate(my_task, self.expression)` and
  then caches the result (`now + duration`) internally
  (`_set_internal_data(event_value=...)`); every timer type (boundary AND
  intermediate catch event) runs through the same class. This evaluation point
  is already exactly the right one ("now, when this particular task actually
  becomes ready") - no need to precompute at instance start. Basis:
  `BpmnWorkflow(spec, script_engine=...)`/`wf.script_engine = ...` (a public,
  settable property, verified via `inspect`) allows a custom
  `SpiffWorkflow.bpmn.script_engine.PythonScriptEngine` instance, whose
  `TaskDataEnvironment(environment_globals={...})` provides additional Python
  functions callable in EVERY script/expression (a name collision with a
  process variable raises a clear `ValueError` there, verified). `_SCRIPT_ENGINE`
  (module singleton) thus provides `business_days(n, calendar_name=None)` - a
  BPMN file writes `business_days(3, "de-national")` into the duration field
  instead of a static `"P3D"` literal. Must be set again after
  `BpmnWorkflow(...)` AND after every `deserialize()` (`wf.script_engine` is
  not part of the serialized state, `BpmnWorkflowConverter.from_dict()` always
  creates a new, GENERIC `BpmnWorkflow` instance without a `script_engine`
  argument when restoring, verified by reading the source). Weekends (Sat/Sun)
  always count as non-working regardless of a specific calendar - a calendar
  itself contains only the ADDITIONAL days (holidays). Without a
  `calendar_name` argument, the installation default calendar
  (`register_business_calendars()`) applies if one is maintained, otherwise
  only the weekend is taken into account. Calendars are deliberately kept as
  an in-memory cache in this module (no synchronous DB access possible from
  the synchronous `has_fired()` evaluation) - `repository.py` keeps the cache
  current after every write access, `main.py`'s lifespan loads it once at
  startup.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from SpiffWorkflow.bpmn.parser.util import full_tag
from SpiffWorkflow.bpmn.script_engine import PythonScriptEngine
from SpiffWorkflow.bpmn.script_engine.python_environment import TaskDataEnvironment
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
    """The BPMN XML is not well-formed, contains no (unique) executable
    process, references an unknown DMN `decisionRef`, or the chosen
    `process_id` does not exist. Deliberately not an exception from
    `repository.py` - this module doesn't know its error hierarchy,
    `repository.py` translates this into its own `InvalidBpmnError` on
    call."""


class DmnParseError(Exception):
    """The DMN XML is not well-formed or does not contain exactly one
    `<decision>` (7.1, P14-S4 - SpiffWorkflow itself supports only exactly
    one decision per DMN file, see module docstring)."""


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
    """Registers the callback that `ConnectorServiceTask._execute()` calls for
    every `taskType=connector_call` service task (P12-S2). A module-wide
    callback instead of a constructor parameter, since SpiffWorkflow's parser
    instantiates the spec class itself (`OVERRIDE_PARSER_CLASSES`, no hook for
    additional constructor arguments). `None` deregisters (tests clean up
    between cases this way)."""
    global _connector_task_handler
    _connector_task_handler = handler


class ConnectorServiceTask(ServiceTask):
    """`bpmn:serviceTask` with `camunda:properties` `taskType=connector_call` (P12-S2,
    see module docstring) - all other service tasks remain unchanged no-ops."""

    def _execute(self, task: Task) -> bool:
        # `_run_hook()` (base class `TaskSpec`) evaluates the return value: a
        # falsy result (e.g. `None`, SpiffWorkflow's own `ServiceTask` default)
        # leaves the task permanently stuck in STARTED instead of becoming
        # COMPLETED - this actually occurred before this `return True` was
        # added. `ScriptTask._execute()` follows the same contract
        # (`return task.workflow.script_engine.execute(...)`).
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
    """`CamundaParser` + `serviceTask` -> `ConnectorServiceTask` (P12-S2) - all other
    overrides (`manualTask`/`userTask`/... for signature/federation, see ADR 0025/0028)
    remain taken over from `CamundaParser` unchanged."""

    OVERRIDE_PARSER_CLASSES = {
        **CamundaParser.OVERRIDE_PARSER_CLASSES,
        # `CamundaTaskParser`, not the base `TaskParser` (SpiffWorkflow's own
        # default for `serviceTask`) - only `CamundaTaskParser` reads
        # `camunda:properties` into `task_spec.extensions` (verified for real:
        # with the base `TaskParser`, `extensions` always stayed empty, so
        # `taskType` was never recognizable).
        full_tag("serviceTask"): (CamundaTaskParser, ConnectorServiceTask),
    }


# `CAMUNDA_CONFIG` instead of `BpmnWorkflowSerializer.configure()` - switching to
# `CamundaParser` maps `userTask` onto Camunda's own `UserTask` spec class instead
# of the BPMN default class; without the matching converter, JSON serialization of
# already-existing fixtures with `userTask` elements (e.g. boundary_timer_on_task.bpmn)
# fails. `ConnectorServiceTask` is additionally mapped to `BpmnTaskSpecConverter` - the
# generic converter that `NoneTask`/`ManualTask`/`UserTask` also use (SpiffWorkflow's
# own `ServiceTask` is not registered in ANY default configuration, "Object of type
# ConnectorServiceTask is not JSON serializable" without this entry - occurred for real).
_SERIALIZER = BpmnWorkflowSerializer(
    BpmnWorkflowSerializer.configure(
        {**CAMUNDA_CONFIG, ConnectorServiceTask: BpmnTaskSpecConverter}
    )
)


class UnknownBusinessCalendarError(Exception):
    """`business_days()` references a `calendar_name` that is not (or no
    longer) registered (P14-S5)."""


# In-memory cache instead of DB access - `has_fired()` (see module docstring)
# calls the expression synchronously from within SpiffWorkflow's engine, an
# async DB read wouldn't be callable there. `repository.py` keeps this cache
# current after every write access, `main.py`'s lifespan loads it once at
# startup.
_business_calendars: dict[str, set[date]] = {}
_default_calendar_name: str | None = None


def register_business_calendars(
    calendars: dict[str, set[date]], *, default_name: str | None
) -> None:
    """Atomically replaces the entire in-memory calendar cache (P14-S5) -
    called after every creation/modification/deletion of a calendar as
    well as once at service start (see `repository.py`/`main.py`)."""
    global _business_calendars, _default_calendar_name
    _business_calendars = calendars
    _default_calendar_name = default_name


def business_days_duration(
    n: int, calendar_name: str | None = None, *, start: datetime | None = None
) -> str:
    """Calculates how many CALENDAR days from `start` (default: now) must
    pass until `n` WORKING days have elapsed, and returns the result as an
    ISO-8601 duration (`"P<x>D"`) - the format that SpiffWorkflow's
    `DurationTimerEventDefinition` itself expects (see module docstring).
    Weekends (Sat/Sun) ALWAYS count as non-working; if `calendar_name` is
    given, its stored days (holidays) additionally count as non-working -
    an unknown name raises `UnknownBusinessCalendarError`. Without
    `calendar_name`, the installation default calendar
    (`register_business_calendars(..., default_name=...)`) applies if one
    is maintained, otherwise only the weekend is taken into account
    (Concept 7.1: "Without a configured calendar, the previous behavior ...
    remains the default" - to be read here as "without a SPECIFIC
    calendar", `business_days()` itself by definition always means at
    least working days rather than calendar days). `start` is deliberately
    keyword-only and not exposed to BPMN authors (see `business_days()`
    below) - a pure testability parameter so tests can specify a fixed
    weekday instead of the actual time."""
    if n < 0:
        raise ValueError(f"n muss >= 0 sein, war {n!r}")
    if calendar_name is not None:
        if calendar_name not in _business_calendars:
            raise UnknownBusinessCalendarError(f"Unbekannter Geschäftskalender {calendar_name!r}")
        non_working = _business_calendars[calendar_name]
    elif _default_calendar_name is not None:
        non_working = _business_calendars.get(_default_calendar_name, set())
    else:
        non_working = set()

    current = start or datetime.now(UTC)
    remaining = n
    elapsed_days = 0
    while remaining > 0:
        elapsed_days += 1
        candidate = (current + timedelta(days=elapsed_days)).date()
        if candidate.weekday() < 5 and candidate not in non_working:
            remaining -= 1
    return f"P{elapsed_days}D"


def business_days(n: int, calendar_name: str | None = None) -> str:
    """The function actually called in the BPMN expression (see
    `_SCRIPT_ENGINE` below) - a thin wrapper without the test-related
    `start` parameter of `business_days_duration()`."""
    return business_days_duration(n, calendar_name)


# Custom `PythonScriptEngine` instance instead of SpiffWorkflow's default
# (P14-S5, see module docstring) - `environment_globals` makes
# `business_days()` callable in EVERY BPMN expression (timer duration,
# script/gateway conditions). A module singleton instead of an instance
# attribute, since both `new_workflow()` and `deserialize()` (each a NEW
# `BpmnWorkflow`, see module docstring) must assign the same engine.
_SCRIPT_ENGINE = PythonScriptEngine(
    environment=TaskDataEnvironment(environment_globals={"business_days": business_days})
)


def _new_parser(xml: str, dmn_definitions: list[str] | None = None) -> DmsBpmnParser:
    parser = DmsBpmnParser()
    try:
        # Load DMN before the BPMN file (order doesn't matter on
        # SpiffWorkflow's side, see module docstring - loaded first here
        # anyway, so a faulty DMN entry doesn't only surface after the BPMN
        # parse attempt). lxml, given an XML encoding declaration
        # (<?xml ... encoding="UTF-8"?>), accepts only bytes, not already-
        # decoded str - hence encode explicitly everywhere, regardless of
        # whether the caller passes str or bytes.
        for dmn_xml in dmn_definitions or []:
            parser.add_dmn_str(dmn_xml.encode("utf-8"))
        parser.add_bpmn_str(xml.encode("utf-8"))
    except Exception as exc:  # SpiffWorkflow/lxml raise various types of their own
        raise BpmnParseError(f"BPMN-Datei nicht parsbar: {exc}") from exc
    return parser


def parse_dmn(xml: str) -> str:
    """Validates a DMN 1.3 file (P14-S4) and returns its internal
    `decision_id` (`<decision id="...">`) - the key that a
    `businessRuleTask`'s `camunda:decisionRef` later references it by (see
    `repository.create_dmn_definition`).

    `BpmnDmnParser.add_dmn_xml()` always creates exactly ONE `dmn_parsers`
    entry per file, keyed by the ID of the FIRST `<decision>` in the
    document (`DMNParser.bpmn_id`, see SpiffWorkflow source) - a second
    `<decision>` in the same document would remain invisible this way,
    `len(parser.dmn_parsers)` would be 1 in EVERY case (empirically
    verified, so this path CANNOT check the multiple-decision limit). The
    actual SpiffWorkflow-native rejection ("Multiple decision tables are
    not current supported") only happens inside `DMNParser.parse()` -
    there, however, only LAZILY, triggered by `BpmnDmnParser.get_decision()`
    when resolving a referencing `businessRuleTask`'s (`camunda:decisionRef`),
    not on loading itself. An upload WITHOUT an accompanying BPMN reference
    would therefore never trigger this error. `parse_dmn()` therefore calls
    `dmn_parser.parse()` explicitly itself here, to enforce the check
    already at upload time. The installed SpiffWorkflow version has its
    own bug here: the `ValidationException` for the multiple-decision case
    passes a `list` as the `node` argument, but `ValidationException.__init__`
    expects an lxml element (`node.tag`) and instead raises an
    `AttributeError` (empirically verified) - the `except Exception` below
    catches both."""
    parser = DmsBpmnParser()
    try:
        parser.add_dmn_str(xml.encode("utf-8"))
        [dmn_parser] = parser.dmn_parsers.values()
        dmn_parser.parse()
    except Exception as exc:
        raise DmnParseError(f"DMN-Datei nicht parsbar: {exc}") from exc
    return dmn_parser.decision.id


def list_process_ids(xml: str, dmn_definitions: list[str] | None = None) -> list[str]:
    """Executable top-level process IDs in the BPMN file (for auto-detection when
    the caller doesn't supply an explicit `process_id`)."""
    return _new_parser(xml, dmn_definitions).get_process_ids()


def parse_bpmn(
    xml: str, process_id: str | None, dmn_definitions: list[str] | None = None
) -> tuple[Any, str]:
    """Parses the BPMN XML and resolves the process ID to instantiate.
    Without `process_id`, resolution is automatic, but only if the file
    contains exactly one executable top-level process - otherwise the
    caller must choose explicitly. `dmn_definitions` (P14-S4): DMN XML
    contents that are loaded into the same parser before the BPMN file -
    any `decisionRef` referenced by a `businessRuleTask` must be among
    them, otherwise `get_spec()` below fails with a SpiffWorkflow-native
    `ValidationException` (translated into `BpmnParseError`)."""
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
    return BpmnWorkflow(spec, script_engine=_SCRIPT_ENGINE)


def serialize(wf: BpmnWorkflow) -> str:
    return _SERIALIZER.serialize_json(wf)


def deserialize(blob: str) -> BpmnWorkflow:
    # `wf.script_engine` is not part of the serialized state - every
    # `deserialize_json()` call internally creates a new, generic
    # `BpmnWorkflow` without a `script_engine` argument (P14-S5, see
    # module docstring) - must be explicitly reassigned afterwards,
    # otherwise `business_days()` would no longer resolve in an already-
    # running instance loaded from the DB.
    wf = _SERIALIZER.deserialize_json(blob)
    wf.script_engine = _SCRIPT_ENGINE
    return wf


def set_initial_data(wf: BpmnWorkflow, data: dict[str, Any]) -> None:
    """Sets process variables before the first `run_ready_steps()` call.
    `wf.set_data()` itself is NOT sufficient for this (verified) - task
    data is passed on to its children when a task completes, not read
    retroactively from the workflow-wide `data` dict. Must therefore be
    set directly on the start task(s) that are ready at this point,
    before any task has run."""
    if not data:
        return
    for task in wf.get_tasks(state=TaskState.READY):
        task.set_data(**data)


def run_ready_steps(wf: BpmnWorkflow) -> None:
    """Executes all ready automatic tasks (Script Tasks etc.) and stops
    before the next Manual/User Task or when the workflow completes."""
    wf.do_engine_steps()


def retry_errored_tasks(wf: BpmnWorkflow) -> int:
    """Resumability for a failed automatic step (7.2, P12-S2): an exception
    in `TaskSpec._run()` (e.g. `ConnectorServiceTask._execute()` when the
    called `serviceUrl` was unreachable) puts the task into `ERROR` (verified
    for real) - a simple repeated `do_engine_steps()` is NOT sufficient to
    retry it (SpiffWorkflow only processes `READY` tasks there).
    `reset_branch()` (official SpiffWorkflow API) resets an `ERROR` task back
    to `FUTURE`/`READY`, retaining the existing task data. Returns the
    number of reset tasks (0 means: nothing to retry, e.g. for an already
    running/completed instance with no error)."""
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
    """Fires due timer/boundary events (SLA time monitoring, P6-S2) and
    reports back which boundary timers actually triggered - regardless of
    whether they cancel the original task (interrupting) or not
    (non-interrupting, see module docstring). The data snapshot (`.data`)
    in particular contains `initial_data` process variables like
    `escalation_email` that were passed through to the boundary task via
    the normal BPMN data flow."""
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
