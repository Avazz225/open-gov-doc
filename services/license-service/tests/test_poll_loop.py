from unittest.mock import AsyncMock

from dms_db_base import make_session_factory
from license_factory import make_license_token
from license_service import license_state
from license_service.poll_loop import UsageClients, run_tick
from license_service.settings import Settings


def _clients(*, sessions=3, storage_bytes=1024**3, documents=50):
    storage_client = AsyncMock()
    storage_client.total_bytes.return_value = storage_bytes
    document_client = AsyncMock()
    document_client.count_active_total.return_value = documents
    auth_client = AsyncMock()
    auth_client.concurrent_session_count.return_value = sessions
    return UsageClients(
        storage_client=storage_client, document_client=document_client, auth_client=auth_client
    )


async def _install(session, **kwargs):
    token = make_license_token(**kwargs)
    await license_state.install(
        session, raw_token=token, installed_by="tester", issued_at=None, expires_at=None
    )
    await session.commit()


async def test_no_license_installed_publishes_nothing(engine):
    settings = Settings()
    publish_event = AsyncMock()
    session_factory = make_session_factory(engine)

    await run_tick(session_factory, _clients(), settings, publish_event)

    publish_event.assert_not_called()


async def test_valid_license_within_limits_publishes_nothing(engine, session):
    await _install(session, max_users=10, storage_limit_gb=100.0, document_limit=1000)
    settings = Settings()
    publish_event = AsyncMock()
    session_factory = make_session_factory(engine)

    await run_tick(session_factory, _clients(), settings, publish_event)

    publish_event.assert_not_called()


async def test_limit_exceeded_publishes_event_once_then_not_again(engine, session):
    await _install(session, max_users=1, storage_limit_gb=100.0, document_limit=1000)
    settings = Settings()
    publish_event = AsyncMock()
    session_factory = make_session_factory(engine)
    clients = _clients(sessions=5)

    await run_tick(session_factory, clients, settings, publish_event)

    assert publish_event.call_count == 1
    event_type, subject, payload, actor = publish_event.call_args[0]
    assert event_type == "license.limit_exceeded"
    assert payload["dimension"] == "users"
    assert actor == "system:license-poll"

    publish_event.reset_mock()
    await run_tick(session_factory, clients, settings, publish_event)

    publish_event.assert_not_called()


async def test_expiring_soon_publishes_event(engine, session):
    await _install(session, expires_in_days=5)
    settings = Settings()
    publish_event = AsyncMock()
    session_factory = make_session_factory(engine)

    await run_tick(session_factory, _clients(), settings, publish_event)

    event_types = [call[0][0] for call in publish_event.call_args_list]
    assert "license.expiring_soon" in event_types


async def test_expired_license_publishes_invalid_event(engine, session):
    await _install(session, expires_in_days=-10, issued_days_ago=400)
    settings = Settings()
    publish_event = AsyncMock()
    session_factory = make_session_factory(engine)

    await run_tick(session_factory, _clients(), settings, publish_event)

    event_types = [call[0][0] for call in publish_event.call_args_list]
    assert "license.invalid" in event_types


async def test_license_for_other_installation_publishes_invalid_event(engine, session):
    """3a/P13-S1: derselbe Flankenerkennungs-Mechanismus greift auch fuer
    eine an eine andere Installation gebundene Lizenz."""
    await _install(session, installation_id="eine-ganz-andere-installation")
    settings = Settings()
    publish_event = AsyncMock()
    session_factory = make_session_factory(engine)

    await run_tick(session_factory, _clients(), settings, publish_event)

    event_types = [call[0][0] for call in publish_event.call_args_list]
    assert "license.invalid" in event_types


async def test_recovering_from_exceeded_allows_new_event_later(engine, session):
    await _install(session, max_users=1)
    settings = Settings()
    session_factory = make_session_factory(engine)

    # Erster Tick: 5 Sessions > 1 -> Ueberschreitung gemeldet.
    await run_tick(session_factory, _clients(sessions=5), settings, AsyncMock())
    # Zweiter Tick: zurueck unter dem Limit -> Snapshot wird zurueckgesetzt.
    await run_tick(session_factory, _clients(sessions=0), settings, AsyncMock())
    # Dritter Tick: wieder ueberschritten -> erneut ein Event (nicht unterdrueckt).
    publish_event = AsyncMock()
    await run_tick(session_factory, _clients(sessions=5), settings, publish_event)

    event_types = [call[0][0] for call in publish_event.call_args_list]
    assert "license.limit_exceeded" in event_types
