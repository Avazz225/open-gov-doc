import pytest
from notification_service import repository


async def test_create_and_send_in_app_is_immediately_sent(session, settings):
    notification = await repository.create_and_send(
        session, settings, channel="in_app", recipient="dept-head", subject="S", body="B"
    )
    assert notification.status == "sent"
    assert notification.sent_at is not None
    assert notification.error is None


async def test_create_and_send_email_via_mailpit_is_sent(session, settings):
    notification = await repository.create_and_send(
        session,
        settings,
        channel="email",
        recipient="empfaenger@example.com",
        subject="S",
        body="B",
    )
    assert notification.status == "sent"


async def test_create_and_send_email_records_failure_when_smtp_unreachable(session, settings):
    settings.smtp_host = "127.0.0.1"
    settings.smtp_port = 1
    notification = await repository.create_and_send(
        session,
        settings,
        channel="email",
        recipient="empfaenger@example.com",
        subject="S",
        body="B",
    )
    assert notification.status == "failed"
    assert notification.error is not None


async def test_create_and_send_webhook_records_failure_when_unreachable(session, settings):
    notification = await repository.create_and_send(
        session,
        settings,
        channel="webhook",
        recipient="http://127.0.0.1:1/nope",
        subject="S",
        body="B",
    )
    assert notification.status == "failed"


async def test_get_notification_unknown_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_notification(session, 999999)


async def test_list_notifications_filters_by_recipient_and_channel(session, settings):
    await repository.create_and_send(
        session, settings, channel="in_app", recipient="alice", subject="S1", body="B"
    )
    await repository.create_and_send(
        session, settings, channel="in_app", recipient="bob", subject="S2", body="B"
    )
    by_recipient = await repository.list_notifications(session, recipient="alice")
    assert len(by_recipient) == 1
    assert by_recipient[0].subject == "S1"

    by_channel = await repository.list_notifications(session, channel="in_app")
    assert len(by_channel) == 2
