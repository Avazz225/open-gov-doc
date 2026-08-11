"""Reiner Unit-Test für `consumer.make_handler` (P17-S3, 4.3/14.2) - anders
als `document_service.consumer`s Pendant keine DB-Session-Factory nötig:
config-service hat kein eigenes Schema, `apply_import` ist ein injizierbarer
Callback (hier ein Fake statt `main._apply_config_document`)."""

from config_service import consumer
from dms_eventbus_client import Event


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


async def test_approved_config_import_calls_apply_import():
    calls = []

    async def fake_apply_import(document, categories):
        calls.append((document, categories))

    handler = consumer.make_handler(fake_apply_import)
    document = {"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"}
    event = _approved_event(
        action_type="config.import", payload={"document": document, "categories": ["roles"]}
    )

    await handler(event.to_bytes())

    assert calls == [(document, ["roles"])]


async def test_unrelated_action_type_is_ignored():
    calls = []

    async def fake_apply_import(document, categories):
        calls.append((document, categories))

    handler = consumer.make_handler(fake_apply_import)
    event = _approved_event(action_type="document.force_unlock", payload={"document_id": "doc-1"})

    await handler(event.to_bytes())

    assert calls == []


async def test_approved_config_import_without_document_is_ignored():
    calls = []

    async def fake_apply_import(document, categories):
        calls.append((document, categories))

    handler = consumer.make_handler(fake_apply_import)
    event = _approved_event(action_type="config.import", payload={"categories": ["roles"]})

    await handler(event.to_bytes())

    assert calls == []


async def test_apply_import_error_is_logged_not_raised():
    """Downstream-Fehler (z. B. unbekannte Schema-Version, nicht erreichbarer
    Owner-Service) dürfen die NATS-Nachricht nicht unbestätigt lassen -
    andernfalls endlose Neuzustellung (siehe Docstring in consumer.py)."""

    async def failing_apply_import(document, categories):
        raise RuntimeError("boom")

    handler = consumer.make_handler(failing_apply_import)
    event = _approved_event(
        action_type="config.import",
        payload={"document": {"schema_version": "1.0"}, "categories": None},
    )

    await handler(event.to_bytes())
