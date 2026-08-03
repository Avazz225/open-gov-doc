import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from storage_service.backends.interface import ObjectNotFoundError
from storage_service.backends.local_backend import LocalFilesystemBackend


@pytest.fixture
def backend(tmp_path):
    return LocalFilesystemBackend(str(tmp_path))


async def test_write_and_read_roundtrip(backend):
    await backend.write("foo/bar.txt", b"hello")

    assert await backend.read("foo/bar.txt") == b"hello"


async def test_exists(backend):
    assert await backend.exists("missing.txt") is False

    await backend.write("present.txt", b"x")

    assert await backend.exists("present.txt") is True


async def test_read_missing_raises(backend):
    with pytest.raises(ObjectNotFoundError):
        await backend.read("missing.txt")


async def test_delete_removes_object(backend):
    await backend.write("todelete.txt", b"x")

    await backend.delete("todelete.txt")

    assert await backend.exists("todelete.txt") is False


async def test_delete_missing_is_noop(backend):
    await backend.delete("never-existed.txt")


async def test_lock_until_and_bypass_governance_are_accepted_but_have_no_effect(backend):
    """Das lokale Dateisystem hat keine Object-Lock-Entsprechung (5.1/5.2a) -
    beide Parameter müssen angenommen werden (einheitliches Interface), aber
    ein "gesperrtes" Objekt bleibt hier trotzdem normal löschbar (ehrlich
    dokumentierte Grenze, siehe retention_guard.py/ADR 0030)."""
    lock_until = datetime.now(UTC) + timedelta(days=1)
    await backend.write("waere-gesperrt.txt", b"x", lock_until=lock_until)

    await backend.delete("waere-gesperrt.txt", bypass_governance=False)

    assert await backend.exists("waere-gesperrt.txt") is False


async def test_checksum_matches_sha256(backend):
    await backend.write("f.txt", b"hello world")

    checksum = await backend.checksum("f.txt")

    assert checksum == hashlib.sha256(b"hello world").hexdigest()


async def test_overwrite_replaces_content(backend):
    await backend.write("f.txt", b"first")
    await backend.write("f.txt", b"second")

    assert await backend.read("f.txt") == b"second"


async def test_path_traversal_is_rejected(backend):
    with pytest.raises(ValueError):
        await backend.write("../escape.txt", b"x")


async def test_no_temp_file_left_behind_after_write(backend, tmp_path):
    await backend.write("clean.txt", b"data")

    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".clean.txt.tmp-")]
    assert leftovers == []
