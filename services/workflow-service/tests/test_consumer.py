"""Reiner Unit-Test für `consumer.make_handler` (Post-Roadmap Phase 21
Session 4, ADR 0087) - gleiches Muster wie `config_service.tests.
test_consumer`: `apply_import` ist ein injizierbarer Callback (hier ein Fake
statt `main._apply_approved_process_definition_import`), keine echte
DB-Session nötig."""

from dms_eventbus_client import Event
from workflow_service import consumer


def _approved_event(*, action_type: str, payload: dict) -> Event:
    return Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-1",
            "action_type": action_type,
            "initiated_by": "admin",
            "approved_by": "supervisor",
            "payload": payload,
        },
    )


async def test_approved_process_definition_import_calls_apply_import():
    calls = []

    async def fake_apply_import(name, bpmn_xml, process_id):
        calls.append((name, bpmn_xml, process_id))

    handler = consumer.make_handler(fake_apply_import)
    event = _approved_event(
        action_type="workflow.process_definition.import",
        payload={"name": "Freigabe", "bpmn_xml": "<xml/>", "process_id": "proc-1"},
    )

    await handler(event.to_bytes())

    assert calls == [("Freigabe", "<xml/>", "proc-1")]


async def test_unrelated_action_type_is_ignored():
    calls = []

    async def fake_apply_import(name, bpmn_xml, process_id):
        calls.append((name, bpmn_xml, process_id))

    handler = consumer.make_handler(fake_apply_import)
    event = _approved_event(action_type="config.import", payload={"document": {}})

    await handler(event.to_bytes())

    assert calls == []


async def test_approved_import_without_name_is_ignored():
    calls = []

    async def fake_apply_import(name, bpmn_xml, process_id):
        calls.append((name, bpmn_xml, process_id))

    handler = consumer.make_handler(fake_apply_import)
    event = _approved_event(
        action_type="workflow.process_definition.import", payload={"bpmn_xml": "<xml/>"}
    )

    await handler(event.to_bytes())

    assert calls == []


async def test_approved_import_without_bpmn_xml_is_ignored():
    calls = []

    async def fake_apply_import(name, bpmn_xml, process_id):
        calls.append((name, bpmn_xml, process_id))

    handler = consumer.make_handler(fake_apply_import)
    event = _approved_event(
        action_type="workflow.process_definition.import", payload={"name": "Freigabe"}
    )

    await handler(event.to_bytes())

    assert calls == []


async def test_apply_import_error_is_logged_not_raised():
    """Downstream-Fehler dürfen die NATS-Nachricht nicht unbestätigt lassen -
    andernfalls endlose Neuzustellung (siehe Docstring in consumer.py)."""

    async def failing_apply_import(name, bpmn_xml, process_id):
        raise RuntimeError("boom")

    handler = consumer.make_handler(failing_apply_import)
    event = _approved_event(
        action_type="workflow.process_definition.import",
        payload={"name": "Freigabe", "bpmn_xml": "<xml/>", "process_id": None},
    )

    await handler(event.to_bytes())
