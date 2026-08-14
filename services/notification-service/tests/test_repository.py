from datetime import UTC, datetime, timedelta

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


async def test_create_and_send_below_max_attempts_stays_failed_and_schedules_retry(
    session, settings
):
    """Post-Roadmap Phase 20 Session 3 (ADR 0079): unterhalb von
    `max_notification_attempts` bleibt `status="failed"` (retry-fähig) mit
    einem gesetzten `next_retry_at`, statt sofort terminal zu werden."""
    settings.max_notification_attempts = 5
    notification = await repository.create_and_send(
        session,
        settings,
        channel="webhook",
        recipient="http://127.0.0.1:1/nope",
        subject="S",
        body="B",
    )
    assert notification.status == "failed"
    assert notification.attempts == 1
    assert notification.next_retry_at is not None


async def test_create_and_send_reaches_failed_permanent_at_max_attempts(session, settings):
    settings.max_notification_attempts = 1
    notification = await repository.create_and_send(
        session,
        settings,
        channel="webhook",
        recipient="http://127.0.0.1:1/nope",
        subject="S",
        body="B",
    )
    assert notification.status == "failed_permanent"
    assert notification.attempts == 1
    assert notification.next_retry_at is None


async def test_list_due_for_retry_excludes_sent_and_failed_permanent(session, settings):
    settings.max_notification_attempts = 5
    retryable = await repository.create_and_send(
        session,
        settings,
        channel="webhook",
        recipient="http://127.0.0.1:1/nope",
        subject="S",
        body="B",
    )
    # Backoff-Fenster kuenstlich in die Vergangenheit versetzt, damit dieser
    # Test gezielt die Status-Filterung prueft, nicht die Backoff-Zeitsteuerung
    # (die hat ihren eigenen Test, test_list_due_for_retry_excludes_notifications_still_in_backoff).
    retryable.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    sent = await repository.create_and_send(
        session, settings, channel="in_app", recipient="alice", subject="S", body="B"
    )
    settings.max_notification_attempts = 1
    permanent = await repository.create_and_send(
        session,
        settings,
        channel="webhook",
        recipient="http://127.0.0.1:1/nope",
        subject="S",
        body="B",
    )

    due_ids = {n.id for n in await repository.list_due_for_retry(session)}

    assert due_ids == {retryable.id}
    assert sent.id not in due_ids
    assert permanent.id not in due_ids


async def test_list_due_for_retry_excludes_notifications_still_in_backoff(session, settings):
    settings.max_notification_attempts = 5
    await repository.create_and_send(
        session,
        settings,
        channel="webhook",
        recipient="http://127.0.0.1:1/nope",
        subject="S",
        body="B",
    )

    due = await repository.list_due_for_retry(session)

    # `compute_backoff_seconds(0)` liegt immer in der (nahen) Zukunft -> noch nicht faellig.
    assert due == []


async def test_retry_now_resets_attempts_and_reattempts_delivery(session, settings):
    settings.max_notification_attempts = 1
    notification = await repository.create_and_send(
        session,
        settings,
        channel="webhook",
        recipient="http://127.0.0.1:1/nope",
        subject="S",
        body="B",
    )
    assert notification.status == "failed_permanent"

    await repository.retry_now(session, settings, notification, max_attempts=1)

    # Ziel bleibt unerreichbar - erneuter Versuch scheitert wieder, landet aber
    # (attempts wurde zurueckgesetzt) erneut sofort bei failed_permanent.
    assert notification.status == "failed_permanent"
    assert notification.attempts == 1


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
