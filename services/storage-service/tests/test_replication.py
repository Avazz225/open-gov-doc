import hashlib
import uuid

import pytest
from storage_service import replication, repository
from storage_service.backends.interface import ObjectNotFoundError, StorageBackend
from storage_service.backends.local_backend import LocalFilesystemBackend


class _AlwaysFailingBackend(StorageBackend):
    """Reale, aber absichtlich immer scheiternde Implementierung des
    Backend-Interfaces (3.6, "Dazustellen"-Prinzip) - dient hier dazu, echte
    Fehlerpfade (Quorum nicht erreicht, Primärziel down, Retry-Queue) ohne
    Mocking der Infrastruktur zu erzeugen."""

    async def write(self, key: str, data: bytes, *, lock_until=None) -> None:
        raise ConnectionError("Ziel nicht erreichbar")

    async def read(self, key: str) -> bytes:
        raise ObjectNotFoundError(key)

    async def delete(self, key: str, *, bypass_governance: bool = False) -> None:
        return None

    async def exists(self, key: str) -> bool:
        return False

    async def checksum(self, key: str) -> str:
        raise ObjectNotFoundError(key)


def _key() -> str:
    return f"test-{uuid.uuid4().hex[:8]}.txt"


async def _make_metadata(session, key: str, checksum: str) -> None:
    await repository.upsert_metadata(
        session,
        object_key=key,
        backend="local",
        checksum_sha256=checksum,
        size_bytes=1,
        content_type=None,
    )


@pytest.fixture
def backend_a(tmp_path):
    return LocalFilesystemBackend(str(tmp_path / "a"))


@pytest.fixture
def backend_b(tmp_path):
    return LocalFilesystemBackend(str(tmp_path / "b"))


async def test_quorum_write_success_both_targets(session, backend_a, backend_b):
    key = _key()
    data = b"hello"
    checksum = hashlib.sha256(data).hexdigest()
    await _make_metadata(session, key, checksum)
    backends = {"a": backend_a, "b": backend_b}

    statuses = await replication.write_with_redundancy(
        session,
        backends=backends,
        targets=["a", "b"],
        strategy="quorum",
        quorum_count=2,
        key=key,
        data=data,
        checksum=checksum,
    )

    assert statuses == {"a": "ok", "b": "ok"}
    assert await backend_a.read(key) == data
    assert await backend_b.read(key) == data
    copies = await repository.list_copies(session, key)
    assert {c.backend_id: c.status for c in copies} == {"a": "ok", "b": "ok"}


async def test_quorum_not_reached_rolls_back_partial_write(session, backend_a):
    key = _key()
    data = b"hello"
    checksum = hashlib.sha256(data).hexdigest()
    await _make_metadata(session, key, checksum)
    backends = {"a": backend_a, "b": _AlwaysFailingBackend()}

    with pytest.raises(replication.QuorumNotReachedError):
        await replication.write_with_redundancy(
            session,
            backends=backends,
            targets=["a", "b"],
            strategy="quorum",
            quorum_count=2,
            key=key,
            data=data,
            checksum=checksum,
        )

    assert await backend_a.exists(key) is False
    # Erfolgreiche Teilkopie ("a") wird zurückgerollt (Datei gelöscht, Zeile
    # entfernt) - die fehlgeschlagene Kopie ("b") bleibt als Diagnose-Eintrag
    # bestehen.
    assert await repository.get_copy(session, key, "a") is None
    failed_copy = await repository.get_copy(session, key, "b")
    assert failed_copy.status == "failed"


async def test_quorum_reached_with_one_failure_when_count_is_one(session, backend_a):
    key = _key()
    data = b"hello"
    checksum = hashlib.sha256(data).hexdigest()
    await _make_metadata(session, key, checksum)
    backends = {"a": backend_a, "b": _AlwaysFailingBackend()}

    statuses = await replication.write_with_redundancy(
        session,
        backends=backends,
        targets=["a", "b"],
        strategy="quorum",
        quorum_count=1,
        key=key,
        data=data,
        checksum=checksum,
    )

    assert statuses == {"a": "ok", "b": "failed"}
    assert await backend_a.read(key) == data


async def test_primary_async_marks_secondary_pending(session, backend_a, backend_b):
    key = _key()
    data = b"hello"
    checksum = hashlib.sha256(data).hexdigest()
    await _make_metadata(session, key, checksum)
    backends = {"a": backend_a, "b": backend_b}

    statuses = await replication.write_with_redundancy(
        session,
        backends=backends,
        targets=["a", "b"],
        strategy="primary_async",
        quorum_count=1,
        key=key,
        data=data,
        checksum=checksum,
    )

    assert statuses == {"a": "ok", "b": "pending"}
    assert await backend_a.read(key) == data
    assert await backend_b.exists(key) is False


async def test_primary_write_failure_raises_without_copy_records(session, backend_a):
    key = _key()
    data = b"hello"
    checksum = hashlib.sha256(data).hexdigest()
    await _make_metadata(session, key, checksum)
    backends = {"a": _AlwaysFailingBackend(), "b": backend_a}

    with pytest.raises(replication.PrimaryWriteError):
        await replication.write_with_redundancy(
            session,
            backends=backends,
            targets=["a", "b"],
            strategy="primary_async",
            quorum_count=1,
            key=key,
            data=data,
            checksum=checksum,
        )

    assert await repository.list_copies(session, key) == []


async def test_read_with_fallback_skips_non_ok_copies(session, backend_a, backend_b):
    key = _key()
    data = b"hello"
    checksum = hashlib.sha256(data).hexdigest()
    await _make_metadata(session, key, checksum)
    await backend_b.write(key, data)
    await repository.record_copy(session, key, "a", status="failed")
    await repository.record_copy(session, key, "b", status="ok", checksum=checksum)

    result = await replication.read_with_fallback(
        session, backends={"a": backend_a, "b": backend_b}, targets=["a", "b"], key=key
    )

    assert result == data


async def test_read_with_fallback_raises_when_no_ok_copy(session, backend_a):
    key = _key()
    await _make_metadata(session, key, "x")
    await repository.record_copy(session, key, "a", status="pending")

    with pytest.raises(ObjectNotFoundError):
        await replication.read_with_fallback(
            session, backends={"a": backend_a}, targets=["a"], key=key
        )


async def test_delete_from_all_removes_files_and_copies(session, backend_a, backend_b):
    key = _key()
    data = b"hello"
    checksum = hashlib.sha256(data).hexdigest()
    await _make_metadata(session, key, checksum)
    backends = {"a": backend_a, "b": backend_b}
    await replication.write_with_redundancy(
        session,
        backends=backends,
        targets=["a", "b"],
        strategy="quorum",
        quorum_count=2,
        key=key,
        data=data,
        checksum=checksum,
    )

    await replication.delete_from_all(session, backends=backends, targets=["a", "b"], key=key)

    assert await backend_a.exists(key) is False
    assert await backend_b.exists(key) is False
    assert await repository.list_copies(session, key) == []


async def test_verify_all_copies_detects_mismatch(session, backend_a, backend_b):
    key = _key()
    data = b"hello"
    checksum = hashlib.sha256(data).hexdigest()
    await _make_metadata(session, key, checksum)
    backends = {"a": backend_a, "b": backend_b}
    await replication.write_with_redundancy(
        session,
        backends=backends,
        targets=["a", "b"],
        strategy="quorum",
        quorum_count=2,
        key=key,
        data=data,
        checksum=checksum,
    )
    await backend_b.write(key, b"corrupted")  # Bit-Rot/Manipulation simulieren

    results = await replication.verify_all_copies(
        session, backends=backends, key=key, expected_checksum=checksum
    )

    by_backend = {r["backend_id"]: r for r in results}
    assert by_backend["a"]["ok"] is True
    assert by_backend["b"]["ok"] is False
    copy_b = await repository.get_copy(session, key, "b")
    assert copy_b.status == "failed"


async def test_verify_all_copies_skips_pending(session, backend_a):
    key = _key()
    checksum = "x"
    await _make_metadata(session, key, checksum)
    await repository.record_copy(session, key, "b", status="pending")

    results = await replication.verify_all_copies(
        session, backends={"a": backend_a, "b": backend_a}, key=key, expected_checksum=checksum
    )

    assert results == [{"backend_id": "b", "status": "pending", "ok": None}]


async def test_process_pending_replicates_secondary(session, backend_a, backend_b):
    key = _key()
    data = b"hello"
    checksum = hashlib.sha256(data).hexdigest()
    await _make_metadata(session, key, checksum)
    backends = {"a": backend_a, "b": backend_b}
    await replication.write_with_redundancy(
        session,
        backends=backends,
        targets=["a", "b"],
        strategy="primary_async",
        quorum_count=1,
        key=key,
        data=data,
        checksum=checksum,
    )

    result = await replication.process_pending(
        session, backends=backends, max_attempts=5, limit=100
    )

    assert result["succeeded"] == 1
    assert await backend_b.read(key) == data
    copy_b = await repository.get_copy(session, key, "b")
    assert copy_b.status == "ok"
    assert copy_b.checksum_sha256 == checksum


async def test_process_pending_marks_permanently_failed_after_max_attempts(session, backend_a):
    key = _key()
    data = b"hello"
    checksum = hashlib.sha256(data).hexdigest()
    await _make_metadata(session, key, checksum)
    backends = {"a": backend_a, "b": _AlwaysFailingBackend()}
    await replication.write_with_redundancy(
        session,
        backends=backends,
        targets=["a", "b"],
        strategy="primary_async",
        quorum_count=1,
        key=key,
        data=data,
        checksum=checksum,
    )

    for _ in range(2):
        await replication.process_pending(session, backends=backends, max_attempts=2, limit=100)

    copy_b = await repository.get_copy(session, key, "b")
    assert copy_b.status == "failed_permanent"
    assert copy_b.attempts == 2

    # ein weiterer Lauf darf die dauerhaft fehlgeschlagene Kopie nicht mehr aufgreifen
    result = await replication.process_pending(
        session, backends=backends, max_attempts=2, limit=100
    )
    assert result["processed"] == 0


async def test_process_pending_without_ok_source_is_reported_failed(session):
    key = _key()
    await _make_metadata(session, key, "x")
    await repository.record_copy(session, key, "b", status="pending")

    result = await replication.process_pending(
        session, backends={"b": _AlwaysFailingBackend()}, max_attempts=5, limit=100
    )

    assert result["processed"] == 1
    assert result["failed"] == 1
    copy_b = await repository.get_copy(session, key, "b")
    assert copy_b.status == "failed"
    assert copy_b.attempts == 1
