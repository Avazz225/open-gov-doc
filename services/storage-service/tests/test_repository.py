import uuid

import pytest
from storage_service import repository


def _key() -> str:
    return f"test-{uuid.uuid4().hex[:8]}.txt"


async def test_upsert_creates_metadata(session):
    key = _key()
    metadata = await repository.upsert_metadata(
        session,
        object_key=key,
        backend="local",
        checksum_sha256="abc123",
        size_bytes=5,
        content_type="text/plain",
    )

    assert metadata.object_key == key
    assert metadata.size_bytes == 5


async def test_upsert_is_update_on_existing_key(session):
    key = _key()
    await repository.upsert_metadata(
        session,
        object_key=key,
        backend="local",
        checksum_sha256="v1",
        size_bytes=1,
        content_type=None,
    )

    updated = await repository.upsert_metadata(
        session,
        object_key=key,
        backend="local",
        checksum_sha256="v2",
        size_bytes=2,
        content_type=None,
    )

    assert updated.checksum_sha256 == "v2"
    assert updated.size_bytes == 2


async def test_get_metadata_not_found_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_metadata(session, "does-not-exist")


async def test_delete_metadata_removes_entry(session):
    key = _key()
    await repository.upsert_metadata(
        session,
        object_key=key,
        backend="local",
        checksum_sha256="x",
        size_bytes=1,
        content_type=None,
    )

    await repository.delete_metadata(session, key)

    with pytest.raises(repository.NotFoundError):
        await repository.get_metadata(session, key)


async def test_delete_metadata_not_found_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.delete_metadata(session, "does-not-exist")
