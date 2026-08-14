import uuid
from datetime import UTC, datetime, timedelta

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


async def test_list_pending_copies_excludes_not_yet_due_backoff(session):
    """Post-Roadmap Phase 20 Session 6 (ADR 0082): eine `failed`-Zeile mit
    einem noch in der Zukunft liegenden `next_retry_at` ist NICHT fällig -
    eine ohne gesetztes `next_retry_at` (NULL, frisch oder noch nie
    fehlgeschlagen) bleibt dagegen immer sofort fällig."""
    key = _key()
    await _make_metadata(session, key)
    not_due = await repository.record_copy(session, key, "s3", status="failed")
    not_due.next_retry_at = datetime.now(UTC) + timedelta(minutes=5)
    other_key = _key()
    await _make_metadata(session, other_key)
    await repository.record_copy(session, other_key, "s3", status="failed")
    await session.flush()

    pending = await repository.list_pending_copies(session, limit=100)

    assert other_key in {c.object_key for c in pending}
    assert key not in {c.object_key for c in pending}


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


async def test_reset_copies_for_backend_resets_status_and_attempts(session):
    key_a = _key()
    key_b = _key()
    await _make_metadata(session, key_a)
    await _make_metadata(session, key_b)
    await repository.record_copy(session, key_a, "s3", status="ok", checksum="x")
    await repository.record_copy(
        session, key_b, "s3", status="failed_permanent", last_error="kaputt", increment_attempt=True
    )
    await repository.record_copy(session, key_a, "local", status="ok", checksum="x")

    count = await repository.reset_copies_for_backend(session, "s3")

    assert count == 2
    for key in (key_a, key_b):
        copy = await repository.get_copy(session, key, "s3")
        assert copy.status == "pending"
        assert copy.attempts == 0
        assert copy.last_error is None
    # Andere Ziele bleiben unberührt.
    assert (await repository.get_copy(session, key_a, "local")).status == "ok"


async def test_reset_copies_for_backend_returns_zero_when_none_exist(session):
    assert await repository.reset_copies_for_backend(session, "nonexistent-target") == 0


async def test_seed_pending_copies_for_new_target_covers_all_uncovered_objects(session):
    key_a = _key()
    key_b = _key()
    await _make_metadata(session, key_a)
    await _make_metadata(session, key_b)
    # key_a hat bereits eine Kopie auf dem neuen Ziel (z. B. aus einem
    # früheren, teilweise gelaufenen Rebalancing-Versuch) - darf nicht
    # doppelt angelegt/überschrieben werden.
    await repository.record_copy(session, key_a, "new-target", status="ok", checksum="x")

    count = await repository.seed_pending_copies_for_new_target(session, "new-target")

    assert count == 1
    assert (await repository.get_copy(session, key_a, "new-target")).status == "ok"
    assert (await repository.get_copy(session, key_b, "new-target")).status == "pending"


async def test_seed_pending_copies_for_new_target_returns_zero_when_no_objects_exist(session):
    assert await repository.seed_pending_copies_for_new_target(session, "new-target") == 0


async def test_count_pending_copies_by_backend(session):
    key = _key()
    await _make_metadata(session, key)
    await repository.record_copy(session, key, "s3", status="pending")
    await repository.record_copy(session, key, "local", status="ok", checksum="x")
    other_key = _key()
    await _make_metadata(session, other_key)
    await repository.record_copy(session, other_key, "s3", status="failed")

    counts = await repository.count_pending_copies_by_backend(session)

    assert counts["s3"] == 2
    assert "local" not in counts


async def test_get_backend_identity_unknown_returns_none(session):
    assert await repository.get_backend_identity(session, "unknown-target") is None


async def test_record_backend_identity_creates_then_updates(session):
    identity = await repository.record_backend_identity(session, "local", "device-1")
    assert identity.device_id == "device-1"
    first_verified_at = identity.verified_at

    updated = await repository.record_backend_identity(session, "local", "device-1")
    assert updated.verified_at >= first_verified_at

    fetched = await repository.get_backend_identity(session, "local")
    assert fetched.device_id == "device-1"


async def test_list_backend_identities_returns_all(session):
    await repository.record_backend_identity(session, "local", "device-a")
    await repository.record_backend_identity(session, "s3", "device-b")

    identities = await repository.list_backend_identities(session)

    assert {i.target_id for i in identities} == {"local", "s3"}


async def test_get_guard_config_creates_default_row(session):
    config = await repository.get_guard_config(session)

    assert config.allow_degraded_start is False


async def test_update_guard_config_persists(session):
    await repository.update_guard_config(session, allow_degraded_start=True)

    fetched = await repository.get_guard_config(session)
    assert fetched.allow_degraded_start is True


async def test_list_target_overrides_empty_without_any_upsert(session):
    """Sparse (Post-Roadmap Phase 22 Session 7, ADR 0092) - anders als
    `GuardConfig`/`OperationalConfig` gibt es hier keine Get-or-create-Zeile,
    solange niemand einen Override gesetzt hat."""
    assert await repository.list_target_overrides(session) == []


async def test_upsert_target_override_creates_and_updates(session):
    created = await repository.upsert_target_override(
        session, "test-target", object_lock_mode="governance", role=None
    )
    assert created.object_lock_mode == "governance"
    assert created.role is None

    updated = await repository.upsert_target_override(
        session, "test-target", object_lock_mode=None, role="archive"
    )
    assert updated.object_lock_mode is None
    assert updated.role == "archive"

    overrides = await repository.list_target_overrides(session)
    assert [o.target_id for o in overrides] == ["test-target"]
