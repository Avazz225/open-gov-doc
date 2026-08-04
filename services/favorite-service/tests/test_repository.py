import pytest
from favorite_service import repository
from favorite_service.schemas import FavoriteCreate


async def test_create_favorite(session):
    favorite = await repository.create_favorite(
        session, FavoriteCreate(user_id="alice", object_type="document", object_id="doc-1")
    )
    assert favorite.id is not None
    assert favorite.user_id == "alice"


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
