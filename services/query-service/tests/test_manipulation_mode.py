from datetime import UTC, datetime, timedelta

from query_service import manipulation_mode


async def test_status_defaults_to_inactive(session):
    status = await manipulation_mode.get_status(session)
    assert status.active is False
    assert manipulation_mode.is_active(status) is False


async def test_activate_sets_expiry_in_the_future(session):
    status = await manipulation_mode.activate(session, activated_by="alice", duration_minutes=10)
    await session.commit()
    assert status.active is True
    assert status.activated_by == "alice"
    assert manipulation_mode.is_active(status) is True
    assert status.expires_at > datetime.now(UTC)


async def test_deactivate_clears_expiry(session):
    await manipulation_mode.activate(session, activated_by="alice", duration_minutes=10)
    await session.commit()

    status = await manipulation_mode.deactivate(session)
    await session.commit()
    assert status.active is False
    assert status.expires_at is None
    assert manipulation_mode.is_active(status) is False


async def test_expired_activation_is_not_active(session):
    status = await manipulation_mode.activate(session, activated_by="alice", duration_minutes=10)
    status.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()

    reloaded = await manipulation_mode.get_status(session)
    assert manipulation_mode.is_active(reloaded) is False
