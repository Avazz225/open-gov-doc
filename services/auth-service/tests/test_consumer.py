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


async def test_approved_activation_enables_superuser_and_publishes(keycloak_admin):
    superuser.ensure_superuser_account(keycloak_admin)
    try:
        published = []

        async def fake_publish(event_type, payload, actor=None):
            published.append((event_type, payload))

        handler = consumer.make_handler(
            keycloak_admin, activation_minutes=30, publish_event=fake_publish
        )

        await handler(_approved_event("auth.superuser.activate"))

        active, _ = superuser.get_status(keycloak_admin)
        assert active is True
        assert len(published) == 1
        assert published[0][0] == "auth.superuser.activated"
        assert published[0][1]["request_id"] == "req-1"
    finally:
        superuser.deactivate(keycloak_admin)


async def test_unrelated_action_type_is_ignored(keycloak_admin):
    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = consumer.make_handler(
        keycloak_admin, activation_minutes=30, publish_event=fake_publish
    )

    await handler(_approved_event("permission.scope_lock.create"))

    assert published == []


async def test_missing_superuser_account_is_logged_not_raised(keycloak_admin):
    """Regression (gleiches Prinzip wie P6-S4s KeyError-Lehre): ein Konsument
    darf nie an unerwartetem Zustand crashen, sonst bleibt die NATS-Nachricht
    unbestätigt und wird endlos erneut zugestellt."""
    existing = keycloak_admin.get_users(
        query={"username": superuser.SUPERUSER_USERNAME, "exact": True}
    )
    for user in existing:
        keycloak_admin.delete_user(user["id"])

    published = []

    async def fake_publish(event_type, payload, actor=None):
        published.append((event_type, payload))

    handler = consumer.make_handler(
        keycloak_admin, activation_minutes=30, publish_event=fake_publish
    )

    await handler(_approved_event("auth.superuser.activate"))  # darf nicht raisen

    assert published == []

    superuser.ensure_superuser_account(keycloak_admin)
