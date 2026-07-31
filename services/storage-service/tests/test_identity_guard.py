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


async def test_bootstrap_of_new_target_seeds_pending_copies_for_existing_objects(session, tmp_path):
    """Rebalancing (3.6/7.2, P5c-S2): ein Ziel, das zum ersten Mal geprüft
    wird (z. B. neu zum Ziel-Set hinzugefügt), muss für bereits vorhandene
    Objekte automatisch `pending`-Kopien anlegen, damit `process-pending`
    sie nachzieht."""
    await repository.upsert_metadata(
        session,
        object_key="doc/a.pdf",
        backend="existing-target",
        checksum_sha256="abc123",
        size_bytes=10,
        content_type=None,
    )
    await repository.upsert_metadata(
        session,
        object_key="doc/b.pdf",
        backend="existing-target",
        checksum_sha256="def456",
        size_bytes=20,
        content_type=None,
    )
    await session.commit()

    new_target_id = _target_id()
    backend = LocalFilesystemBackend(str(tmp_path))
    verified = await identity_guard.check_target_identity(session, new_target_id, backend)
    await session.commit()

    assert verified is True
    copies = {
        c.object_key: c
        for c in [
            await repository.get_copy(session, "doc/a.pdf", new_target_id),
            await repository.get_copy(session, "doc/b.pdf", new_target_id),
        ]
    }
    assert copies["doc/a.pdf"].status == "pending"
    assert copies["doc/b.pdf"].status == "pending"


async def test_bootstrap_without_existing_objects_seeds_nothing(session, tmp_path):
    target_id = _target_id()
    backend = LocalFilesystemBackend(str(tmp_path))

    await identity_guard.check_target_identity(session, target_id, backend)
    await session.commit()

    assert await repository.count_pending_copies_by_backend(session) == {}


async def test_reidentify_writes_new_marker_when_device_is_blank(session, tmp_path):
    """Simuliert den Standardfall eines Datenträger-Tauschs: das neue Gerät
    ist leer (keine Marker-Datei) - `reidentify_target` prägt es wie beim
    Erststart-Bootstrap."""
    target_id = _target_id()
    backend = LocalFilesystemBackend(str(tmp_path))
    await identity_guard.check_target_identity(session, target_id, backend)
    await session.commit()
    # Als reiner String festhalten, nicht als ORM-Objektreferenz - der
    # Identity-Map-Fast-Path von `session.get()` würde sonst dasselbe
    # Python-Objekt liefern, das `reidentify_target` gleich darauf mutiert
    # (gleiche Falle wie das dokumentierte `session.delete()`-Muster).
    old_device_id = (await repository.get_backend_identity(session, target_id)).device_id

    await backend.delete(identity_guard.IDENTITY_KEY)
    new_identity = await identity_guard.reidentify_target(session, target_id, backend)
    await session.commit()

    assert new_identity.device_id != old_device_id
    marker = await backend.read(identity_guard.IDENTITY_KEY)
    assert json.loads(marker)["device_id"] == new_identity.device_id


async def test_reidentify_adopts_devices_own_existing_marker(session, tmp_path):
    """Das neue Gerät hat bereits eine eigene Marker-Datei (z. B. zuvor an
    anderer Stelle im selben System geprägt) - diese wird als neuer
    Referenzwert übernommen, statt überschrieben zu werden."""
    target_id = _target_id()
    backend = LocalFilesystemBackend(str(tmp_path))
    await identity_guard.check_target_identity(session, target_id, backend)
    await session.commit()

    await backend.write(
        identity_guard.IDENTITY_KEY, json.dumps({"device_id": "geraet-von-woanders"}).encode()
    )

    new_identity = await identity_guard.reidentify_target(session, target_id, backend)
    await session.commit()

    assert new_identity.device_id == "geraet-von-woanders"


async def test_reidentify_resets_existing_copies_to_pending(session, tmp_path):
    target_id = _target_id()
    backend = LocalFilesystemBackend(str(tmp_path))
    await identity_guard.check_target_identity(session, target_id, backend)
    await repository.upsert_metadata(
        session,
        object_key="doc/c.pdf",
        backend=target_id,
        checksum_sha256="abc123",
        size_bytes=10,
        content_type=None,
    )
    await repository.record_copy(session, "doc/c.pdf", target_id, status="ok", checksum="abc123")
    await session.commit()

    await identity_guard.reidentify_target(session, target_id, backend)
    await session.commit()

    copy = await repository.get_copy(session, "doc/c.pdf", target_id)
    assert copy.status == "pending"
