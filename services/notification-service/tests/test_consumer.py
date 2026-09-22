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


async def test_lock_reminder_event_creates_in_app_notification_for_the_lock_holder(
    engine, settings
):
    """Post-Roadmap Phase 30 Session 4 (ADR 0111) - the first notification
    hook for document-service's lock feature. Unlike the deletion
    reminders, there is no separate `notify_email` - the lock holder
    (`locked_by`) is directly the in-app recipient."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="document.lock.reminder",
        service_name="document-service",
        subject="doc-20",
        payload={"title": "Vertrag.pdf", "locked_by": "alice"},
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "in_app"
    assert notifications[0].recipient == "alice"
    assert "Vertrag.pdf" in notifications[0].body
    assert len(published) == 1


async def test_lock_reminder_includes_a_direct_link_when_configured(engine, settings):
    """Post-Roadmap Phase 29 (ADR 0109), wired up for this new use case in
    Phase 30 Session 4."""

    async def fake_publish(event_type, subject, payload, actor=None):
        pass

    configured = settings.model_copy(update={"user_ui_public_base_url": "http://localhost:3000"})
    handler = consumer.make_handler(_session_factory(engine), configured, fake_publish)
    event = Event(
        event_type="document.lock.reminder",
        service_name="document-service",
        subject="doc-21",
        payload={"title": "Vertrag.pdf", "locked_by": "alice"},
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert "http://localhost:3000/?document=doc-21" in notifications[0].body


async def test_lock_reminder_uses_configured_template(engine, settings):
    await _configure_template(
        engine,
        use_case="document.lock.reminder",
        recipient_domain_pattern=None,
        subject_template="[Vorlage] {title}",
        body_template="{title} (id={document_id}) gesperrt von {locked_by}",
    )

    async def fake_publish(event_type, subject, payload, actor=None):
        pass

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="document.lock.reminder",
        service_name="document-service",
        subject="doc-22",
        payload={"title": "Vertrag.pdf", "locked_by": "alice"},
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert notifications[0].subject == "[Vorlage] Vertrag.pdf"
    assert notifications[0].body == "Vertrag.pdf (id=doc-22) gesperrt von alice"


async def test_virus_scan_infected_event_creates_in_app_notification_for_the_uploader(
    engine, settings
):
    """Post-Roadmap Phase 44 Session 4 - `virus_scan.completed` was
    already published unconditionally by `virus-scan-service` since it
    was built; this is the first-ever consumer, closing a real,
    previously-open gap (ADR 0073's own documented "no notification of
    the uploader on a hit")."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="virus_scan.completed",
        service_name="virus-scan-service",
        subject="scan-1",
        payload={
            "document_id": None,
            "filename": "invoice.exe",
            "status": "infected",
            "threat_name": "Eicar-Test-Signature",
            "created_by": "alice",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "in_app"
    assert notifications[0].recipient == "alice"
    assert "invoice.exe" in notifications[0].body
    assert "Eicar-Test-Signature" in notifications[0].body
    assert len(published) == 1


async def test_virus_scan_clean_event_creates_no_notification(engine, settings):
    """The same subject also fires for a clean result (`virus-scan-
    service` publishes `virus_scan.completed` unconditionally) - only an
    actual hit is worth notifying anyone about."""

    async def fake_publish(event_type, subject, payload, actor=None):
        pass

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="virus_scan.completed",
        service_name="virus-scan-service",
        subject="scan-2",
        payload={
            "document_id": "doc-30",
            "filename": "invoice.pdf",
            "status": "clean",
            "threat_name": None,
            "created_by": "alice",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert notifications == []


async def test_virus_scan_infected_event_includes_a_direct_link_when_document_id_known(
    engine, settings
):
    """`document_id` is usually `None` for an infected result (the
    document is never created for one, see `virus-scan-service`'s own
    `POST /scan` docstring) - but when it IS known (e.g. a re-scan of an
    already-registered document), the link should use `document_id`,
    NOT the event's own `subject` (the scan's own id, a different
    resource type entirely)."""

    async def fake_publish(event_type, subject, payload, actor=None):
        pass

    configured = settings.model_copy(update={"user_ui_public_base_url": "http://localhost:3000"})
    handler = consumer.make_handler(_session_factory(engine), configured, fake_publish)
    event = Event(
        event_type="virus_scan.completed",
        service_name="virus-scan-service",
        subject="scan-3",
        payload={
            "document_id": "doc-31",
            "filename": "invoice.exe",
            "status": "infected",
            "threat_name": "Eicar-Test-Signature",
            "created_by": "alice",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert "http://localhost:3000/?document=doc-31" in notifications[0].body


async def test_task_claim_abandoned_event_creates_in_app_notification_for_the_claimant(
    engine, settings
):
    """Post-Roadmap Phase 35 Session 3 (ADR 0145) - the notification half
    of the feature ADR 0121 scoped out. Same shape as the lock-reminder
    tests above: the claim's own `principal_id` (the person who most
    plausibly forgot about it) is directly the in-app recipient."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="workflow.task_claim.abandoned",
        service_name="workflow-service",
        subject="instance-30",
        payload={
            "task_id": "task-1",
            "task_name": "Prüfung",
            "principal_id": "bob",
            "business_key": "case-30",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "in_app"
    assert notifications[0].recipient == "bob"
    assert "Prüfung" in notifications[0].body
    assert len(published) == 1


async def test_task_claim_abandoned_includes_a_direct_link_when_configured(engine, settings):
    async def fake_publish(event_type, subject, payload, actor=None):
        pass

    configured = settings.model_copy(
        update={"reviewer_ui_public_base_url": "http://localhost:3005"}
    )
    handler = consumer.make_handler(_session_factory(engine), configured, fake_publish)
    event = Event(
        event_type="workflow.task_claim.abandoned",
        service_name="workflow-service",
        subject="instance-31",
        payload={
            "task_id": "task-1",
            "task_name": "Prüfung",
            "principal_id": "bob",
            "business_key": "case-31",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert "http://localhost:3005/?instance=instance-31" in notifications[0].body


async def test_task_claim_abandoned_uses_configured_template(engine, settings):
    await _configure_template(
        engine,
        use_case="workflow.task_claim.abandoned",
        recipient_domain_pattern=None,
        subject_template="[Vorlage] {task_name}",
        body_template="{task_name} (Vorgang {instance_id}) seit längerem bei {principal_id}",
    )

    async def fake_publish(event_type, subject, payload, actor=None):
        pass

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="workflow.task_claim.abandoned",
        service_name="workflow-service",
        subject="instance-32",
        payload={
            "task_id": "task-1",
            "task_name": "Prüfung",
            "principal_id": "bob",
            "business_key": "case-32",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert notifications[0].subject == "[Vorlage] Prüfung"
    assert notifications[0].body == "Prüfung (Vorgang instance-32) seit längerem bei bob"


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


# --- Configurable email templates (post-roadmap phase 30, ADR 0111) -------
# All 9 handlers above are already exercised WITHOUT any configured
# template row (the fixture default: an empty `email_template` table) - they
# keep producing the same hardcoded text as before the P30-S3 migration,
# proving the "no row = zero behavior change" guarantee. The tests below
# cover the other half: WITH a configured row, the handler renders it
# instead.


async def _configure_template(
    engine, *, use_case, recipient_domain_pattern, subject_template, body_template
):
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await repository.upsert_email_template(
            session,
            use_case=use_case,
            recipient_domain_pattern=recipient_domain_pattern,
            subject_template=subject_template,
            body_template=body_template,
        )
        await session.commit()


async def test_task_escalated_uses_configured_template_when_present(engine, settings):
    await _configure_template(
        engine,
        use_case="workflow.task.escalated",
        recipient_domain_pattern=None,
        subject_template="[Vorlage] {task_name}",
        body_template="Instanz {instance_id}, Business Key {business_key}",
    )
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="workflow.task.escalated",
        service_name="workflow-service",
        subject="instance-10",
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
    in_app = next(n for n in notifications if n.channel == "in_app")
    email = next(n for n in notifications if n.channel == "email")
    assert in_app.subject == "[Vorlage] Freigabe"
    assert email.subject == "[Vorlage] Freigabe"
    assert email.body == "Instanz instance-10, Business Key doc-1"


async def test_federation_inbound_received_uses_configured_template(engine, settings):
    await _configure_template(
        engine,
        use_case="workflow.federation.inbound_received",
        recipient_domain_pattern=None,
        subject_template="[Vorlage] {process_type}",
        body_template="Von {from_installation_id}, Instanz {instance_id}",
    )
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="workflow.federation.inbound_received",
        service_name="workflow-service",
        subject="instance-11",
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
    email = next(n for n in notifications if n.channel == "email")
    assert email.subject == "[Vorlage] external-review"
    assert email.body == "Von install-abc, Instanz instance-11"


async def test_deletion_reminder_uses_configured_template(engine, settings):
    await _configure_template(
        engine,
        use_case="document.deletion.reminder",
        recipient_domain_pattern=None,
        subject_template="[Vorlage] {title}",
        body_template="{title} (id={document_id}) - {action} am {retention_until}",
    )
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="document.deletion.reminder",
        service_name="document-service",
        subject="doc-9",
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
    assert email.subject == "[Vorlage] Vertrag.pdf"
    assert "physisch zwangsgelöscht" in email.body


async def test_deletion_reminder_domain_specific_template_wins_over_catchall(engine, settings):
    await _configure_template(
        engine,
        use_case="document.deletion.reminder",
        recipient_domain_pattern=None,
        subject_template="Catchall: {title}",
        body_template="Catchall body",
    )
    await _configure_template(
        engine,
        use_case="document.deletion.reminder",
        recipient_domain_pattern="example.com",
        subject_template="Domain: {title}",
        body_template="Domain body",
    )
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="document.deletion.reminder",
        service_name="document-service",
        subject="doc-12",
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
    in_app = next(n for n in notifications if n.channel == "in_app")
    assert email.subject == "Domain: Vertrag.pdf"
    # "unassigned" (the in-app recipient) has no "@" - never matches a
    # domain-specific row, only the catchall.
    assert in_app.subject == "Catchall: Vertrag.pdf"


async def test_folder_deletion_reminder_uses_configured_template(engine, settings):
    await _configure_template(
        engine,
        use_case="folder.deletion.reminder",
        recipient_domain_pattern=None,
        subject_template="[Vorlage] {name}",
        body_template="{name} (id={folder_id}) - {action} am {retention_until}",
    )
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="folder.deletion.reminder",
        service_name="folder-service",
        subject="folder-9",
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
    assert email.subject == "[Vorlage] Projektakte"


async def test_superuser_activated_uses_configured_template(engine, settings):
    await _configure_template(
        engine,
        use_case="auth.superuser.activated",
        recipient_domain_pattern=None,
        subject_template="[Vorlage] Break-Glass",
        body_template="Läuft ab: {expires_at}",
    )
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
    assert notifications[0].subject == "[Vorlage] Break-Glass"
    assert notifications[0].body == "Läuft ab: 2026-01-01T00:30:00+00:00"


async def test_maintenance_mode_activated_uses_configured_template(engine, settings):
    await _configure_template(
        engine,
        use_case="permission.maintenance_mode.activated",
        recipient_domain_pattern=None,
        subject_template="[Vorlage] Notfallsperre",
        body_template="Von {triggered_by}: {reason}",
    )
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
    assert notifications[0].subject == "[Vorlage] Notfallsperre"
    assert notifications[0].body == "Von alice: Verdacht auf unautorisierten Zugriff"


async def test_license_limit_exceeded_uses_configured_template(engine, settings):
    await _configure_template(
        engine,
        use_case="license.limit_exceeded",
        recipient_domain_pattern=None,
        subject_template="[Vorlage] Limit",
        body_template="{dimension}: {current}/{limit}",
    )
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
    assert notifications[0].subject == "[Vorlage] Limit"
    assert notifications[0].body == "documents: 1200/1000"


async def test_license_expiring_soon_uses_configured_template(engine, settings):
    await _configure_template(
        engine,
        use_case="license.expiring_soon",
        recipient_domain_pattern=None,
        subject_template="[Vorlage] Ablauf",
        body_template="Noch {days_remaining} Tage",
    )
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
    assert notifications[0].subject == "[Vorlage] Ablauf"
    assert notifications[0].body == "Noch 12 Tage"


async def test_license_invalid_uses_configured_template(engine, settings):
    await _configure_template(
        engine,
        use_case="license.invalid",
        recipient_domain_pattern=None,
        subject_template="[Vorlage] Ungültig",
        body_template="Grund: {reason}",
    )
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
    assert notifications[0].subject == "[Vorlage] Ungültig"
    assert notifications[0].body == "Grund: Lizenz abgelaufen"


async def test_deletion_reminder_falls_back_when_template_has_unknown_placeholder(engine, settings):
    """A misconfigured template (references a placeholder that isn't in the
    catalog for this use_case, e.g. a typo) fails loudly at render time
    (`UnknownPlaceholderError`) - `_render_or_fallback` catches it and keeps
    the existing hardcoded text instead of ever sending a broken email with
    a literal unfilled placeholder in it."""
    await _configure_template(
        engine,
        use_case="document.deletion.reminder",
        recipient_domain_pattern=None,
        subject_template="{nonexistent_placeholder}",
        body_template="B",
    )
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="document.deletion.reminder",
        service_name="document-service",
        subject="doc-13",
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
    assert email.subject == "Löschfrist erreicht bald: Vertrag.pdf"


# --- Force-unlock execution feedback (4.2/4.3, P71-S1) ----------------------


async def test_lock_force_released_notifies_the_original_lock_holder(engine, settings):
    """Closes ADR 0022's own named gap ("no execution feedback channel") -
    `document-service` has published `document.lock.force_released`
    unconditionally since P6-S4, just never consumed here before."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="document.lock.force_released",
        service_name="document-service",
        subject="doc-30",
        payload={
            "original_locked_by": "alice",
            "released_by": "bob",
            "reason": "Urlaub",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "in_app"
    assert notifications[0].recipient == "alice"
    assert "bob" in notifications[0].body
    assert len(published) == 1


async def test_force_unlock_failed_notifies_the_approver(engine, settings):
    """Counterpart - the approver whose already-approved request could not
    actually be executed is the one who needs to know, not the original
    lock holder (who was never affected)."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="document.force_unlock.failed",
        service_name="document-service",
        subject="doc-31",
        payload={
            "approval_request_id": "req-1",
            "released_by": "bob",
            "reason": "Sperre bereits anderweitig aufgehoben",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "in_app"
    assert notifications[0].recipient == "bob"
    assert "anderweitig aufgehoben" in notifications[0].body


# --- Four-eyes approval lifecycle feedback (4.3, P71-S1) --------------------


async def test_approval_approved_notifies_the_initiator(engine, settings):
    """The initiator of an approval request previously had no way to learn
    its outcome except by polling `GET /approval-requests/{id}` directly -
    `request_id` comes from the payload, not `event.subject`
    (`permission-service`'s own `publish_event` never sets a subject)."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        payload={
            "request_id": "req-2",
            "action_type": "document.classification.declassify",
            "initiated_by": "alice",
            "approved_by": "carol",
            "payload": {},
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "in_app"
    assert notifications[0].recipient == "alice"
    assert "carol" in notifications[0].body


async def test_approval_rejected_notifies_the_initiator_with_reason(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="permission.approval.rejected",
        service_name="permission-service",
        payload={
            "request_id": "req-3",
            "action_type": "document.classification.declassify",
            "initiated_by": "alice",
            "rejected_by": "carol",
            "reason": "Unzureichend begründet",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "in_app"
    assert notifications[0].recipient == "alice"
    assert "Unzureichend begründet" in notifications[0].body


# --- Storage-service alerting (3.6, P71-S1) ----------------------------------


async def test_storage_replication_failed_permanent_notifies_the_storage_admin(engine, settings):
    """`storage-service`'s first ever event bus connection - permanently-
    failed replication was "logged instead of alerted" before this."""
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="storage.replication.failed_permanent",
        service_name="storage-service",
        subject="documents/doc-1/abc",
        payload={
            "object_key": "documents/doc-1/abc",
            "backend_id": "s3-secondary",
            "attempts": 5,
            "error": "connection refused",
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "email"
    assert notifications[0].recipient == settings.storage_admin_email
    assert "s3-secondary" in notifications[0].body


async def test_storage_verify_mismatch_notifies_the_storage_admin(engine, settings):
    published = []

    async def fake_publish(event_type, subject, payload, actor=None):
        published.append((event_type, subject, payload))

    handler = consumer.make_handler(_session_factory(engine), settings, fake_publish)
    event = Event(
        event_type="storage.object_verify.mismatch",
        service_name="storage-service",
        subject="documents/doc-2/def",
        payload={
            "object_key": "documents/doc-2/def",
            "backend_ids": ["local", "s3-secondary"],
        },
    )

    await handler(event.to_bytes())

    session_factory = _session_factory(engine)
    async with session_factory() as session:
        notifications = await repository.list_notifications(session)
    assert len(notifications) == 1
    assert notifications[0].channel == "email"
    assert notifications[0].recipient == settings.storage_admin_email
    assert "local" in notifications[0].body
