import json
import uuid

from storage_service import identity_guard, repository
from storage_service.backends.interface import StorageBackend
from storage_service.backends.local_backend import LocalFilesystemBackend


class _UnreachableBackend(StorageBackend):
    """Reale, aber absichtlich immer scheiternde Implementierung (3.6,
    "Dazustellen"-Prinzip, gleiches Muster wie test_replication.py's
    `_AlwaysFailingBackend`) - hier mit einem generischen Verbindungsfehler
    statt `ObjectNotFoundError`, um "Backend nicht erreichbar" von "Marker-
    Datei fehlt" zu unterscheiden."""

    async def write(self, key: str, data: bytes) -> None:
        raise ConnectionError("Ziel nicht erreichbar")

    async def read(self, key: str) -> bytes:
        raise ConnectionError("Ziel nicht erreichbar")

    async def delete(self, key: str) -> None:
        raise ConnectionError("Ziel nicht erreichbar")

    async def exists(self, key: str) -> bool:
        raise ConnectionError("Ziel nicht erreichbar")

    async def checksum(self, key: str) -> str:
        raise ConnectionError("Ziel nicht erreichbar")


def _target_id() -> str:
    return f"target-{uuid.uuid4().hex[:8]}"


async def test_first_ever_check_bootstraps_and_writes_marker(session, tmp_path):
    target_id = _target_id()
    backend = LocalFilesystemBackend(str(tmp_path))

    verified = await identity_guard.check_target_identity(session, target_id, backend)
    await session.commit()

    assert verified is True
    stored = await repository.get_backend_identity(session, target_id)
    assert stored is not None
    marker = await backend.read(identity_guard.IDENTITY_KEY)
    assert json.loads(marker)["device_id"] == stored.device_id


async def test_second_check_with_matching_marker_stays_verified(session, tmp_path):
    target_id = _target_id()
    backend = LocalFilesystemBackend(str(tmp_path))
    await identity_guard.check_target_identity(session, target_id, backend)
    await session.commit()

    verified = await identity_guard.check_target_identity(session, target_id, backend)

    assert verified is True


async def test_missing_marker_after_known_identity_is_unverified(session, tmp_path):
    """Simuliert einen Datenträger-Wechsel: die Identitätsdatei ist plötzlich
    weg (z. B. leeres/falsches Ersatzmedium), obwohl bereits eine bekannte
    Geräte-ID in der DB existiert."""
    target_id = _target_id()
    backend = LocalFilesystemBackend(str(tmp_path))
    await identity_guard.check_target_identity(session, target_id, backend)
    await session.commit()

    await backend.delete(identity_guard.IDENTITY_KEY)

    verified = await identity_guard.check_target_identity(session, target_id, backend)

    assert verified is False
    # Der zuletzt bekannte Referenzwert bleibt unverändert - kein stilles Überschreiben.
    stored = await repository.get_backend_identity(session, target_id)
    assert stored is not None


async def test_mismatched_marker_is_unverified(session, tmp_path):
    target_id = _target_id()
    backend = LocalFilesystemBackend(str(tmp_path))
    await identity_guard.check_target_identity(session, target_id, backend)
    await session.commit()

    await backend.write(
        identity_guard.IDENTITY_KEY, json.dumps({"device_id": "ein-anderes-geraet"}).encode()
    )

    verified = await identity_guard.check_target_identity(session, target_id, backend)

    assert verified is False


async def test_unreachable_backend_with_no_known_identity_is_unverified(session):
    target_id = _target_id()

    verified = await identity_guard.check_target_identity(session, target_id, _UnreachableBackend())

    assert verified is False
    assert await repository.get_backend_identity(session, target_id) is None


async def test_unreachable_backend_with_known_identity_is_unverified(session, tmp_path):
    target_id = _target_id()
    backend = LocalFilesystemBackend(str(tmp_path))
    await identity_guard.check_target_identity(session, target_id, backend)
    await session.commit()

    verified = await identity_guard.check_target_identity(session, target_id, _UnreachableBackend())

    assert verified is False
