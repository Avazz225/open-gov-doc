from dms_eventbus_client import Event


def test_roundtrip_preserves_fields():
    event = Event(
        event_type="registry.instance.registered",
        service_name="registry-service",
        subject="doc-1",
        payload={"service_type": "document-service"},
    )

    restored = Event.from_bytes(event.to_bytes())

    assert restored.event_id == event.event_id
    assert restored.event_type == event.event_type
    assert restored.service_name == event.service_name
    assert restored.subject == event.subject
    assert restored.payload == event.payload
    assert restored.occurred_at == event.occurred_at


def test_event_id_is_auto_generated_and_unique():
    a = Event(event_type="x", service_name="svc")
    b = Event(event_type="x", service_name="svc")

    assert a.event_id != b.event_id
