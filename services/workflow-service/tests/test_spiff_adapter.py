import time

import pytest
from workflow_service import spiff_adapter as sa


def test_list_process_ids_returns_executable_processes(manual_task_bpmn):
    assert sa.list_process_ids(manual_task_bpmn) == ["Process_cozt5fu"]


def test_parse_bpmn_auto_detects_single_process(manual_task_bpmn):
    _, process_id = sa.parse_bpmn(manual_task_bpmn, None)
    assert process_id == "Process_cozt5fu"


def test_parse_bpmn_with_explicit_process_id(manual_task_bpmn):
    _, process_id = sa.parse_bpmn(manual_task_bpmn, "Process_cozt5fu")
    assert process_id == "Process_cozt5fu"


def test_parse_bpmn_unknown_process_id_raises(manual_task_bpmn):
    with pytest.raises(sa.BpmnParseError):
        sa.parse_bpmn(manual_task_bpmn, "does_not_exist")


def test_parse_bpmn_invalid_xml_raises():
    with pytest.raises(sa.BpmnParseError):
        sa.parse_bpmn("not valid xml", None)


def test_no_tasks_process_completes_immediately(no_tasks_bpmn):
    spec, _ = sa.parse_bpmn(no_tasks_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.run_ready_steps(wf)
    assert sa.is_completed(wf) is True
    assert sa.ready_manual_tasks(wf) == []


def test_script_task_runs_automatically_and_leaves_manual_task_ready(manual_task_bpmn):
    """Deckt genau den in P6-S1 zugesagten Umfang ab: Automatic Tasks (Script
    Task) laufen ohne Zutun, Manual Tasks bleiben bereit stehen."""
    spec, _ = sa.parse_bpmn(manual_task_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.run_ready_steps(wf)

    assert sa.is_completed(wf) is False
    ready = sa.ready_manual_tasks(wf)
    assert len(ready) == 1
    assert ready[0].name == "manual"


def test_manual_task_without_extensions_has_empty_extensions_dict(manual_task_bpmn):
    spec, _ = sa.parse_bpmn(manual_task_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.run_ready_steps(wf)
    ready = sa.ready_manual_tasks(wf)
    assert ready[0].extensions == {}


def test_signature_task_extensions_are_parsed(signature_task_bpmn):
    """Grundlage des Signature Task (3.10, P6-S7): `camunda:properties`
    werden über den CamundaParser-Wechsel in `task_spec.extensions` sichtbar,
    ohne dass der BPMN-Task-Typ (`manualTask`) sich ändert."""
    spec, _ = sa.parse_bpmn(signature_task_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.run_ready_steps(wf)
    ready = sa.ready_manual_tasks(wf)
    assert len(ready) == 1
    assert ready[0].extensions == {"taskType": "signature", "requiredLevel": "aes"}


def test_serialize_deserialize_roundtrip_preserves_ready_task_id(manual_task_bpmn):
    spec, _ = sa.parse_bpmn(manual_task_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.run_ready_steps(wf)
    ready_before = sa.ready_manual_tasks(wf)

    blob = sa.serialize(wf)
    wf2 = sa.deserialize(blob)
    ready_after = sa.ready_manual_tasks(wf2)

    assert ready_before[0].id == ready_after[0].id
    assert sa.is_completed(wf2) is False


def test_complete_task_advances_workflow_to_completion(manual_task_bpmn):
    spec, _ = sa.parse_bpmn(manual_task_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.run_ready_steps(wf)
    ready = sa.ready_manual_tasks(wf)

    task = sa.find_ready_task(wf, ready[0].id)
    assert task is not None
    sa.complete_task(task, {"decision": "approved"})
    sa.run_ready_steps(wf)

    assert sa.is_completed(wf) is True
    assert sa.ready_manual_tasks(wf) == []


def test_find_ready_task_returns_none_for_unknown_id(manual_task_bpmn):
    spec, _ = sa.parse_bpmn(manual_task_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.run_ready_steps(wf)
    assert sa.find_ready_task(wf, "does-not-exist") is None


def test_set_initial_data_is_visible_to_downstream_tasks(manual_task_bpmn):
    """`wf.set_data()` reicht dafür NICHT aus (empirisch verifiziert beim
    Implementieren) - Daten müssen vor dem ersten Lauf auf dem bereiten
    Start-Task gesetzt werden, damit sie an Kind-Tasks weitergereicht werden."""
    spec, _ = sa.parse_bpmn(manual_task_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.set_initial_data(wf, {"foo": "bar"})
    sa.run_ready_steps(wf)

    ready = sa.ready_manual_tasks(wf)
    assert ready[0].data == {"foo": "bar"}


def test_lane_is_extracted_when_bpmn_model_defines_lanes(lanes_bpmn):
    spec, _ = sa.parse_bpmn(lanes_bpmn, "lanes")
    wf = sa.new_workflow(spec)
    sa.run_ready_steps(wf)

    ready = sa.ready_manual_tasks(wf)
    assert len(ready) == 1
    assert ready[0].lane == "A"


def test_lane_is_none_when_bpmn_model_has_no_lanes(manual_task_bpmn):
    spec, _ = sa.parse_bpmn(manual_task_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.run_ready_steps(wf)

    ready = sa.ready_manual_tasks(wf)
    assert ready[0].lane is None


def test_check_timers_fires_non_interrupting_boundary_event(boundary_timer_bpmn):
    """P6-S2 (SLA-Zeitüberwachung): non-interrupting Boundary-Timer feuert die
    Eskalationsverzweigung, während der ursprüngliche Task bereit bleibt. `escalation_email`
    aus `initial_data` muss im Datenschnappschuss des gefeuerten Events sichtbar sein -
    normaler BPMN-Datenfluss vom Start-Task bis zum Boundary-Task."""
    spec, _ = sa.parse_bpmn(boundary_timer_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.set_initial_data(wf, {"escalation_email": "supervisor@example.com"})
    sa.run_ready_steps(wf)
    assert sa.check_timers(wf) == []  # Timer noch nicht fällig

    time.sleep(0.1)
    fired = sa.check_timers(wf)

    assert len(fired) == 1
    assert fired[0].data["escalation_email"] == "supervisor@example.com"
    # Non-interrupting: der ursprüngliche Task bleibt bereit und ist normal abschließbar.
    ready = sa.ready_manual_tasks(wf)
    assert len(ready) == 1
    task = sa.find_ready_task(wf, ready[0].id)
    sa.complete_task(task, {})
    sa.run_ready_steps(wf)
    assert sa.is_completed(wf) is True


def test_check_timers_returns_empty_list_when_nothing_fired(manual_task_bpmn):
    spec, _ = sa.parse_bpmn(manual_task_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.run_ready_steps(wf)
    assert sa.check_timers(wf) == []


def test_connector_service_task_calls_registered_handler_and_merges_result(
    connector_service_task_bpmn,
):
    """Generische Connector-Service-Task-Plumbing (7.1 "Auslösen eines
    Connector-Aufrufs", P12-S0-Fund, gebaut in P12-S2 für migration-service,
    aber bewusst nicht migrationsspezifisch)."""
    calls = []

    def handler(extensions, data):
        calls.append((dict(extensions), dict(data)))
        return {"result": "ok"}

    sa.register_connector_task_handler(handler)
    try:
        spec, _ = sa.parse_bpmn(connector_service_task_bpmn, None)
        wf = sa.new_workflow(spec)
        sa.set_initial_data(wf, {"input": 1})
        sa.run_ready_steps(wf)

        assert sa.is_completed(wf) is True
        assert calls == [
            (
                {
                    "taskType": "connector_call",
                    "serviceUrl": "http://connector-stub.invalid/step",
                },
                {"input": 1},
            )
        ]
    finally:
        sa.register_connector_task_handler(None)


def test_connector_service_task_without_handler_raises(connector_service_task_bpmn):
    sa.register_connector_task_handler(None)
    spec, _ = sa.parse_bpmn(connector_service_task_bpmn, None)
    wf = sa.new_workflow(spec)
    with pytest.raises(RuntimeError):
        sa.run_ready_steps(wf)


def test_serialize_deserialize_roundtrip_preserves_connector_task_result(
    connector_service_task_bpmn,
):
    sa.register_connector_task_handler(lambda extensions, data: {"result": "ok"})
    try:
        spec, _ = sa.parse_bpmn(connector_service_task_bpmn, None)
        wf = sa.new_workflow(spec)
        sa.run_ready_steps(wf)
        blob = sa.serialize(wf)
        wf2 = sa.deserialize(blob)
        assert sa.is_completed(wf2) is True
    finally:
        sa.register_connector_task_handler(None)


def test_retry_errored_tasks_resumes_after_a_failed_connector_call(
    connector_service_task_bpmn,
):
    """Resumability (7.2, P12-S2): ein fehlgeschlagener Connector-Aufruf lässt den
    Task in `ERROR` zurück, `retry_errored_tasks()` versetzt ihn zurück auf
    `READY` - ein anschliessendes `run_ready_steps()` versucht ihn erneut."""
    attempts = {"count": 0}

    def flaky_handler(extensions, data):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("Ziel nicht erreichbar")
        return {"result": "ok"}

    sa.register_connector_task_handler(flaky_handler)
    try:
        spec, _ = sa.parse_bpmn(connector_service_task_bpmn, None)
        wf = sa.new_workflow(spec)

        with pytest.raises(RuntimeError):
            sa.run_ready_steps(wf)
        assert sa.is_completed(wf) is False

        resumed = sa.retry_errored_tasks(wf)
        assert resumed == 1
        sa.run_ready_steps(wf)

        assert sa.is_completed(wf) is True
        assert attempts["count"] == 2
    finally:
        sa.register_connector_task_handler(None)


def test_timer_duration_can_reference_a_process_variable(variable_duration_timer_bpmn):
    """`DurationTimerEventDefinition.has_fired()` evaluiert `timeDuration` über den
    Script-Engine statt es als statisches Literal zu behandeln - ein Bare-Identifier
    referenziert damit eine Prozessvariable (P12-S2, Grundlage für migration-service's
    konfigurierbare Löschfrist: `retention_duration` wird erst beim Instanzstart aus
    `retention_days` berechnet, ist also zur BPMN-Autorenzeit nicht bekannt)."""
    spec, _ = sa.parse_bpmn(variable_duration_timer_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.set_initial_data(wf, {"retention_duration": "PT0.05S"})
    sa.run_ready_steps(wf)
    assert sa.is_completed(wf) is False

    time.sleep(0.2)
    sa.check_timers(wf)

    assert sa.is_completed(wf) is True


def test_retry_errored_tasks_returns_zero_when_nothing_is_errored(manual_task_bpmn):
    spec, _ = sa.parse_bpmn(manual_task_bpmn, None)
    wf = sa.new_workflow(spec)
    sa.run_ready_steps(wf)
    assert sa.retry_errored_tasks(wf) == 0


def test_parse_dmn_returns_decision_id(approval_level_dmn):
    assert sa.parse_dmn(approval_level_dmn) == "approval-level"


def test_parse_dmn_invalid_xml_raises():
    with pytest.raises(sa.DmnParseError):
        sa.parse_dmn("not valid xml")


def test_parse_dmn_multiple_decisions_raises(multi_decision_dmn):
    with pytest.raises(sa.DmnParseError):
        sa.parse_dmn(multi_decision_dmn)


def test_parse_bpmn_business_rule_task_without_dmn_definitions_raises(business_rule_task_bpmn):
    """`camunda:decisionRef="approval-level"` referenziert eine Decision, die hier
    nicht geladen wird - `get_spec()` muss dabei mit einer SpiffWorkflow-eigenen
    `ValidationException` fehlschlagen (übersetzt in `BpmnParseError`), DMN muss vor
    dem referenzierenden BPMN geladen sein (P14-S4, siehe Moduldocstring)."""
    with pytest.raises(sa.BpmnParseError):
        sa.parse_bpmn(business_rule_task_bpmn, None)


def test_business_rule_task_evaluates_decision_and_writes_output(
    business_rule_task_bpmn, approval_level_dmn
):
    """Ende-zu-Ende: ein `businessRuleTask` mit `camunda:decisionRef="approval-level"`
    wertet die geladene DMN-Entscheidungstabelle transparent über
    `do_engine_steps()` aus (P14-S4, kein eigener `DMNEngine`-Aufruf in diesem
    Modul nötig)."""
    spec, _ = sa.parse_bpmn(business_rule_task_bpmn, None, dmn_definitions=[approval_level_dmn])
    wf = sa.new_workflow(spec)
    sa.set_initial_data(wf, {"amount": 1500})
    sa.run_ready_steps(wf)

    assert sa.is_completed(wf) is True
    [leaf] = [t for t in wf.get_tasks() if t.task_spec.name == "EndEvent_1"]
    assert leaf.data["level"] == "abteilungsleiter"


def test_business_rule_task_evaluates_lower_amount_rule(
    business_rule_task_bpmn, approval_level_dmn
):
    spec, _ = sa.parse_bpmn(business_rule_task_bpmn, None, dmn_definitions=[approval_level_dmn])
    wf = sa.new_workflow(spec)
    sa.set_initial_data(wf, {"amount": 100})
    sa.run_ready_steps(wf)

    assert sa.is_completed(wf) is True
    [leaf] = [t for t in wf.get_tasks() if t.task_spec.name == "EndEvent_1"]
    assert leaf.data["level"] == "sachbearbeiter"


def test_business_rule_task_unknown_decision_ref_raises(
    business_rule_task_unknown_decision_bpmn, approval_level_dmn
):
    with pytest.raises(sa.BpmnParseError):
        sa.parse_bpmn(
            business_rule_task_unknown_decision_bpmn, None, dmn_definitions=[approval_level_dmn]
        )
