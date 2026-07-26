import uuid

import pytest
from storage_service import repository


def _key() -> str:
    return f"test-{uuid.uuid4().hex[:8]}.txt"


async def _make_metadata(session, key: str) -> None:
    """object_copy hat eine FK auf object_metadata - Copy-Tests brauchen
    daher immer zuerst eine Metadaten-Zeile für den jeweiligen Key."""
    await repository.upsert_metadata(
        session,
        object_key=key,
        backend="local",
        checksum_sha256="x",
        size_bytes=1,
        content_type=None,
    )


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


async def test_record_copy_creates_and_updates(session):
    key = _key()
    await _make_metadata(session, key)
    await repository.record_copy(session, key, "local", status="pending")

    copy = await repository.get_copy(session, key, "local")
    assert copy.status == "pending"
    assert copy.attempts == 0

    await repository.record_copy(session, key, "local", status="ok", checksum="abc")

    copy = await repository.get_copy(session, key, "local")
    assert copy.status == "ok"
    assert copy.checksum_sha256 == "abc"


async def test_record_copy_increment_attempt(session):
    key = _key()
    await _make_metadata(session, key)
    await repository.record_copy(session, key, "s3", status="failed", increment_attempt=True)
    await repository.record_copy(session, key, "s3", status="failed", increment_attempt=True)

    copy = await repository.get_copy(session, key, "s3")
    assert copy.attempts == 2


async def test_list_copies_returns_all_backends_for_key(session):
    key = _key()
    await _make_metadata(session, key)
    await repository.record_copy(session, key, "local", status="ok", checksum="x")
    await repository.record_copy(session, key, "s3", status="pending")

    copies = await repository.list_copies(session, key)

    assert {c.backend_id for c in copies} == {"local", "s3"}


async def test_get_any_ok_copy_ignores_pending(session):
    key = _key()
    await _make_metadata(session, key)
    await repository.record_copy(session, key, "s3", status="pending")

    assert await repository.get_any_ok_copy(session, key) is None

    await repository.record_copy(session, key, "local", status="ok", checksum="x")

    ok_copy = await repository.get_any_ok_copy(session, key)
    assert ok_copy.backend_id == "local"


async def test_list_pending_copies_excludes_ok_and_permanent(session):
    key = _key()
    await _make_metadata(session, key)
    await repository.record_copy(session, key, "local", status="ok", checksum="x")
    await repository.record_copy(session, key, "s3", status="pending")
    other_key = _key()
    await _make_metadata(session, other_key)
    await repository.record_copy(session, other_key, "s3", status="failed_permanent")

    pending = await repository.list_pending_copies(session, limit=100)

    backend_ids_for_key = {c.backend_id for c in pending if c.object_key == key}
    assert backend_ids_for_key == {"s3"}
    assert all(c.object_key != other_key for c in pending)


async def test_delete_copy_removes_single_backend_row(session):
    key = _key()
    await _make_metadata(session, key)
    await repository.record_copy(session, key, "local", status="ok", checksum="x")
    await repository.record_copy(session, key, "s3", status="ok", checksum="x")

    await repository.delete_copy(session, key, "s3")

    remaining = await repository.list_copies(session, key)
    assert {c.backend_id for c in remaining} == {"local"}


async def test_delete_copies_for_key_removes_all(session):
    key = _key()
    await _make_metadata(session, key)
    await repository.record_copy(session, key, "local", status="ok", checksum="x")
    await repository.record_copy(session, key, "s3", status="ok", checksum="x")

    await repository.delete_copies_for_key(session, key)

    assert await repository.list_copies(session, key) == []
