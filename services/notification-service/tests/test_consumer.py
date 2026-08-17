from dms_db_base import make_session_factory
from dms_eventbus_client import Event
from notification_service import consumer, repository


def _session_factory(engine):
    return make_session_factory(engine)


async def test_escalated_event_creates_in_app_and_email_notification(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
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


async def test_escalated_event_email_includes_a_direct_link_when_configured(engine, settings):
    """Post-Roadmap Phase 29 (ADR 0109) - `event.subject` (the instance ID)
    becomes a reviewer-ui direct link in the email body, only when
    `reviewer_ui_public_base_url` is actually configured."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    configured = settings.model_copy(
        update={"reviewer_ui_public_base_url": "http://localhost:3005"}
    )
    handler = consumer.make_handler(_session_factory(engine), configured, fake_publish)
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
    email = next(n for n in notifications if n.channel == "email")
    assert "http://localhost:3005/?instance=instance-1" in email.body


async def test_escalated_event_email_has_no_link_when_base_url_unconfigured(engine, settings):
    """`settings.reviewer_ui_public_base_url` is `None` by default (see
    `test_consumer.py`'s `settings` fixture / ADR 0105) - no link is
    fabricated in that case."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="workflow.task.escalated",
        service_name="workflow-service",
        subject="instance-3",
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
    email = next(n for n in notifications if n.channel == "email")
    assert "http" not in email.body


async def test_escalated_event_without_email_creates_only_in_app_notification(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
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

    async def fake_publish(event_type, subject, payload, actor=None):
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


async def test_federation_inbound_received_with_notify_email_creates_in_app_and_email(
    engine, settings
):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="workflow.federation.inbound_received",
        service_name="workflow-service",
        subject="instance-3",
        payload={
            "business_key": None,
            "from_installation_id": "install-abc",
            "process_type": "external-review",
            "notify_email": "reviewer@example.com",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert {n.channel for n in notifications} == {"in_app", "email"}
    in_app = next(n for n in notifications if n.channel == "in_app")
    assert in_app.recipient == "unassigned"
    email = next(n for n in notifications if n.channel == "email")
    assert email.recipient == "reviewer@example.com"
    assert "install-abc" in email.body
    assert len(published) == 2


async def test_federation_inbound_received_without_notify_email_creates_only_in_app(
    engine, settings
):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="workflow.federation.inbound_received",
        service_name="workflow-service",
        subject="instance-4",
        payload={
            "business_key": None,
            "from_installation_id": "install-abc",
            "process_type": "external-review",
            "notify_email": None,
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "in_app"
    assert len(published) == 1


async def test_folder_deletion_reminder_with_notify_email_creates_in_app_and_email(
    engine, settings
):
    """1:1 dasselbe Muster wie `document.deletion.reminder` (P7-S1), hier für
    `folder.deletion.reminder` (5.2a, P7-S1b)."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="folder.deletion.reminder",
        service_name="folder-service",
        subject="folder-1",
        payload={
            "name": "Projektakte",
            "retention_until": "2030-01-01T00:00:00+00:00",
            "full_deletion": True,
            "notify_email": "records@example.com",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert {n.channel for n in notifications} == {"in_app", "email"}
    email = next(n for n in notifications if n.channel == "email")
    assert email.recipient == "records@example.com"
    assert "Projektakte" in email.body
    assert len(published) == 2


async def test_deletion_reminder_with_notify_email_creates_in_app_and_email(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="document.deletion.reminder",
        service_name="document-service",
        subject="doc-1",
        payload={
            "title": "Vertrag.pdf",
            "retention_until": "2030-01-01T00:00:00+00:00",
            "full_deletion": True,
            "notify_email": "records@example.com",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert {n.channel for n in notifications} == {"in_app", "email"}
    email = next(n for n in notifications if n.channel == "email")
    assert email.recipient == "records@example.com"
    assert "Vertrag.pdf" in email.body
    assert len(published) == 2


async def test_deletion_reminder_email_includes_a_direct_link_when_configured(engine, settings):
    """Post-Roadmap Phase 29 (ADR 0109)."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    configured = settings.model_copy(update={"user_ui_public_base_url": "http://localhost:3000"})
    handler = consumer.make_handler(_session_factory(engine), configured, fake_publish)
    event = Event(
        event_type="document.deletion.reminder",
        service_name="document-service",
        subject="doc-2",
        payload={
            "title": "Vertrag.pdf",
            "retention_until": "2030-01-01T00:00:00+00:00",
            "full_deletion": True,
            "notify_email": "records@example.com",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    email = next(n for n in notifications if n.channel == "email")
    assert "http://localhost:3000/?document=doc-2" in email.body


async def test_folder_deletion_reminder_email_includes_a_direct_link_when_configured(
    engine, settings
):
    """Post-Roadmap Phase 29 (ADR 0109)."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    configured = settings.model_copy(update={"user_ui_public_base_url": "http://localhost:3000"})
    handler = consumer.make_handler(_session_factory(engine), configured, fake_publish)
    event = Event(
        event_type="folder.deletion.reminder",
        service_name="folder-service",
        subject="folder-2",
        payload={
            "name": "Projektakte",
            "retention_until": "2030-01-01T00:00:00+00:00",
            "full_deletion": True,
            "notify_email": "records@example.com",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    email = next(n for n in notifications if n.channel == "email")
    assert "http://localhost:3000/?folder=folder-2" in email.body


async def test_deletion_reminder_without_notify_email_creates_only_in_app(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="document.deletion.reminder",
        service_name="document-service",
        subject="doc-2",
        payload={
            "title": "Notiz.txt",
            "retention_until": "2030-01-01T00:00:00+00:00",
            "full_deletion": False,
            "notify_email": None,
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "in_app"
    assert len(published) == 1


async def test_maintenance_mode_activated_event_creates_security_officer_email(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
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


async def test_license_limit_exceeded_event_creates_license_admin_email(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="license.limit_exceeded",
        service_name="license-service",
        payload={"dimension": "documents", "current": 1200, "limit": 1000},
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "email"
    assert notifications[0].recipient == settings.license_admin_email
    assert "documents" in notifications[0].body
    assert len(published) == 1


async def test_license_expiring_soon_event_creates_license_admin_email(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="license.expiring_soon",
        service_name="license-service",
        payload={"days_remaining": 12},
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].recipient == settings.license_admin_email
    assert "12" in notifications[0].body
    assert len(published) == 1


async def test_license_invalid_event_creates_license_admin_email(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="license.invalid",
        service_name="license-service",
        payload={"reason": "Lizenz abgelaufen"},
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].recipient == settings.license_admin_email
    assert "abgelaufen" in notifications[0].body
    assert len(published) == 1
