import pytest
from favorite_service import repository
from favorite_service.schemas import FavoriteCreate


async def test_create_favorite(session):
    favorite = await repository.create_favorite(
        session, FavoriteCreate(user_id="alice", object_type="document", object_id="doc-1")
    )
    assert favorite.id is not None
    assert favorite.user_id == "alice"


async def test_create_case_favorite(session):
    """Phase 45 Session 2 - `"case"` as a third `object_type`, see
    `test_api.py::test_create_list_and_delete_a_case_favorite` for the
    full deferral rationale."""
    favorite = await repository.create_favorite(
        session, FavoriteCreate(user_id="alice", object_type="case", object_id="case-1")
    )
    assert favorite.object_type == "case"
    assert favorite.object_id == "case-1"


async def test_create_duplicate_raises(session):
    payload = FavoriteCreate(user_id="alice", object_type="document", object_id="doc-1")
    await repository.create_favorite(session, payload)
    with pytest.raises(repository.DuplicateError):
        await repository.create_favorite(session, payload)


async def test_delete_unknown_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.delete_favorite(
            session, user_id="alice", object_type="document", object_id="unknown"
        )


async def test_delete_removes_row(session):
    await repository.create_favorite(
        session, FavoriteCreate(user_id="alice", object_type="folder", object_id="folder-1")
    )
    await repository.delete_favorite(
        session, user_id="alice", object_type="folder", object_id="folder-1"
    )
    results = await repository.list_favorites(session, user_id="alice")
    assert results == []


async def test_list_orders_newest_first(session):
    await repository.create_favorite(
        session, FavoriteCreate(user_id="alice", object_type="document", object_id="doc-1")
    )
    await repository.create_favorite(
        session, FavoriteCreate(user_id="alice", object_type="document", object_id="doc-2")
    )
    results = await repository.list_favorites(session, user_id="alice")
    assert [r.object_id for r in results] == ["doc-2", "doc-1"]


async def test_only_one_replica_claims_a_reminder_tick(engine):
    """P90-S1 (ADR 0246): while one transaction holds the poll's advisory
    lock, another tick gets None and claims nothing."""
    from datetime import UTC, datetime, timedelta

    from dms_db_base import make_session_factory
    from favorite_service import reminders
    from favorite_service.schemas import ReminderCreate
    from sqlalchemy import text

    factory = make_session_factory(engine)
    now = datetime.now(UTC)
    async with factory() as session:
        await reminders.create_reminder(
            session,
            ReminderCreate(
                user_id="alice",
                object_type="folder",
                object_id="f-1",
                due_at=now - timedelta(minutes=1),
            ),
            now=now,
        )
        await session.commit()

    async with factory() as holder, factory() as other:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": reminders.REMINDER_POLL_LOCK_KEY}
        )
        assert await reminders.claim_due(other, now=now) is None
        await other.rollback()
        await holder.rollback()

    async with factory() as session:
        claimed = await reminders.claim_due(session, now=now)
        await session.commit()
    assert [c.object_id for c in claimed] == ["f-1"]
