from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from notification_service import repository
from notification_service.main import _run_retry_tick, app, settings


@pytest.fixture
def client():
    # Startet den echten Lifespan (NATS-Producer/-Consumer, Retry-Poll-Loop) -
    # `_run_retry_tick` unten publiziert echte `notification.sent`/`.failed`-
    # Events und braucht dafuer `app.state.producer` (gleiches Muster wie
    # test_api.py's `client`-Fixture).
    with TestClient(app) as c:
        yield c


async def test_run_retry_tick_redelivers_a_due_notification(client, session_factory, session):
    """Post-Roadmap Phase 20 Session 3 (ADR 0079): der Retry-Poll-Loop-Tick
    greift eine faellige, retry-faehige Notification auf und versucht sie
    erneut zuzustellen (Ziel bleibt hier absichtlich unerreichbar, geprüft
    wird die Attempt-Buchführung nach dem erneuten Fehlschlag)."""
    original_max_attempts = settings.max_notification_attempts
    settings.max_notification_attempts = 5
    try:
        notification = await repository.create_and_send(
            session,
            settings,
            channel="webhook",
            recipient="http://127.0.0.1:1/nope",
            subject="S",
            body="B",
        )
        await session.commit()
        assert notification.status == "failed"
        assert notification.attempts == 1

        # next_retry_at liegt normalerweise in der (nahen) Zukunft - fuer einen
        # deterministischen Tick-Test direkt in die Vergangenheit gesetzt.
        notification.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

        await _run_retry_tick(session_factory)

        async with session_factory() as fresh_session:
            fresh = await repository.get_notification(fresh_session, notification.id)
            assert fresh.status == "failed"
            assert fresh.attempts == 2
    finally:
        settings.max_notification_attempts = original_max_attempts


async def test_run_retry_tick_skips_notifications_not_yet_due(client, session_factory, session):
    original_max_attempts = settings.max_notification_attempts
    settings.max_notification_attempts = 5
    try:
        notification = await repository.create_and_send(
            session,
            settings,
            channel="webhook",
            recipient="http://127.0.0.1:1/nope",
            subject="S",
            body="B",
        )
        await session.commit()
        assert notification.attempts == 1

        await _run_retry_tick(session_factory)

        async with session_factory() as fresh_session:
            fresh = await repository.get_notification(fresh_session, notification.id)
            # next_retry_at liegt noch in der Zukunft (Full-Jitter-Backoff nach
            # dem ersten Fehlschlag) - der Tick darf sie nicht anfassen.
            assert fresh.attempts == 1
    finally:
        settings.max_notification_attempts = original_max_attempts
