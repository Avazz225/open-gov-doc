from auth_service import consumer, superuser
from dms_eventbus_client import Event


def _approved_event(action_type: str, payload: dict | None = None) -> bytes:
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-1",
            "action_type": action_type,
            "initiated_by": "alice",
            "approved_by": "bob",
            "payload": payload or {},
        },
    )
    return event.to_bytes()


async def test_approved_activation_enables_superuser_and_publishes(session_factory):
    await superuser.ensure_superuser_account(session_factory)
    try:
        published = []

        async def fake_publish(event_type, payload, actor=None):
            published.append((event_type, payload))

        handler = consumer.make_handler(
            session_factory, activation_minutes=30, publish_event=fake_publish
        )

        await handler(_approved_event("auth.superuser.activate"))

        active, _ = await superuser.get_status(session_factory)
        assert active is True
        assert len(published) == 1
        assert published[0][0] == "auth.superuser.activated"
        assert published[0][1]["request_id"] == "req-1"
    finally:
        await superuser.deactivate(session_factory)


async def test_unrelated_action_type_is_ignored(session_factory):
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = consumer.make_handler(
        session_factory, activation_minutes=30, publish_event=fake_publish
    )

    await handler(_approved_event("permission.scope_lock.create"))

    assert published == []


async def test_missing_superuser_account_is_logged_not_raised(session_factory):
    """Regression (gleiches Prinzip wie P6-S4s KeyError-Lehre): ein Konsument
    darf nie an unerwartetem Zustand crashen, sonst bleibt die NATS-Nachricht
    unbestätigt und wird endlos erneut zugestellt. `_clean_tables` (conftest,
    autouse) hat `technical_account` bereits geleert - kein Konto existiert
    zu Beginn dieses Tests."""
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = consumer.make_handler(
        session_factory, activation_minutes=30, publish_event=fake_publish
    )

    await handler(_approved_event("auth.superuser.activate"))  # darf nicht raisen

    assert published == []
