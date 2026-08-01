from dms_db_base import make_session_factory
from dms_eventbus_client import Event
from notification_service import consumer, repository


def _session_factory(engine):
    return make_session_factory(engine)


async def test_escalated_event_creates_in_app_and_email_notification(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="workflow.task.escalated",
        service_name="workflow-service",
        subject="instance-1",
        payload={
            "process_definition_id": 1,
            "business_key": "doc-1",
            "task_name": "Freigabe",
            "lane": "Vorgesetzte",
            "escalation_email": "supervisor@example.com",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert {n.channel for n in notifications} == {"in_app", "email"}
    in_app = next(n for n in notifications if n.channel == "in_app")
    assert in_app.recipient == "Vorgesetzte"
    email = next(n for n in notifications if n.channel == "email")
    assert email.recipient == "supervisor@example.com"
    assert {e[0] for e in published} <= {"notification.sent", "notification.failed"}
    assert len(published) == 2


async def test_escalated_event_without_email_creates_only_in_app_notification(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="workflow.task.escalated",
        service_name="workflow-service",
        subject="instance-2",
        payload={
            "process_definition_id": 1,
            "business_key": None,
            "task_name": "Freigabe",
            "lane": None,
            "escalation_email": None,
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "in_app"
    assert notifications[0].recipient == "unassigned"
    assert len(published) == 1


async def test_superuser_activated_event_creates_security_officer_email(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="auth.superuser.activated",
        service_name="auth-service",
        payload={"request_id": "req-1", "expires_at": "2026-01-01T00:30:00+00:00"},
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "email"
    assert notifications[0].recipient == settings.security_officer_email
    assert len(published) == 1


async def test_maintenance_mode_activated_event_creates_security_officer_email(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="permission.maintenance_mode.activated",
        service_name="permission-service",
        payload={"triggered_by": "alice", "reason": "Verdacht auf unautorisierten Zugriff"},
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "email"
    assert notifications[0].recipient == settings.security_officer_email
    assert "alice" in notifications[0].body
    assert len(published) == 1
