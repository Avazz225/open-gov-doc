import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from storage_service.backends.azure_backend import AzureBlobBackend
from storage_service.backends.interface import ObjectNotFoundError

# Fester, öffentlich von Microsoft dokumentierter Azurite-Dev-Verbindungsstring
# ("devstoreaccount1" + wohlbekannter Account-Key) - kein echtes Geheimnis,
# funktioniert nur gegen den lokalen Azurite-Emulator. Analog zu
# TEST_S3_ENDPOINT_URL/ACCESS_KEY/SECRET_KEY bei test_s3_backend.py
# env-überschreibbar, falls Azurite unter anderer Adresse läuft.
DEFAULT_AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
    "K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)
CONNECTION_STRING = os.environ.get(
    "TEST_AZURE_CONNECTION_STRING", DEFAULT_AZURITE_CONNECTION_STRING
)


@pytest.fixture
async def backend():
    container = f"test-{uuid.uuid4().hex[:8]}"
    b = AzureBlobBackend(connection_string=CONNECTION_STRING, container=container)
    await b.ensure_container()
    yield b
    async with b._service_client() as service:  # noqa: SLF001 - Testaufräumen, kein Produktionscode
        await service.delete_container(container)


async def test_ensure_container_is_idempotent(backend):
    await backend.ensure_container()  # zweiter Aufruf darf nicht scheitern


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


async def test_delete_missing_is_idempotent(backend):
    """Wie S3Backend/LocalFilesystemBackend: Löschen eines nicht (mehr)
    vorhandenen Objekts ist kein Fehler."""
    await backend.delete("never-existed.txt")


async def test_checksum_matches_sha256(backend):
    await backend.write("f.txt", b"hello world")

    checksum = await backend.checksum("f.txt")

    assert checksum == hashlib.sha256(b"hello world").hexdigest()


async def test_overwrite_replaces_content(backend):
    await backend.write("f.txt", b"first")
    await backend.write("f.txt", b"second")

    assert await backend.read("f.txt") == b"second"


async def test_lock_until_is_accepted_but_ignored(backend):
    """Dokumentierter No-Op (siehe AzureBlobBackend-Docstring): anders als
    S3Backend mit object_lock_enabled blockiert ein gesetztes `lock_until`
    das Löschen NICHT - Azurite unterstützt keine Immutability-Policies, ein
    ungetesteter "Schutz" wäre vorgetäuscht. Die eigentliche Durchsetzung
    übernimmt `retention_guard.py` unabhängig vom Backend-Typ."""
    lock_until = datetime.now(UTC) + timedelta(days=1)

    await backend.write("gesperrt.txt", b"schuetzenswert", lock_until=lock_until)
    await backend.delete("gesperrt.txt")  # kein bypass_governance nötig, kein Fehler

    assert await backend.exists("gesperrt.txt") is False


async def test_bypass_governance_flag_is_accepted_but_has_no_effect(backend):
    await backend.write("f2.txt", b"x")

    await backend.delete("f2.txt", bypass_governance=True)

    assert await backend.exists("f2.txt") is False
