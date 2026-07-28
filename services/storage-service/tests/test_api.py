import hashlib
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from storage_service.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _key() -> str:
    return f"test-{uuid.uuid4().hex[:8]}/file.txt"


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["primary_target"] == "local"
    assert body["targets"] == ["local"]


def test_upload_download_roundtrip(client):
    key = _key()
    content = b"hello world"

    upload = client.put(f"/objects/{key}", content=content, headers={"content-type": "text/plain"})
    assert upload.status_code == 201
    assert upload.json()["checksum_sha256"] == hashlib.sha256(content).hexdigest()
    assert upload.json()["size_bytes"] == len(content)

    download = client.get(f"/objects/{key}")
    assert download.status_code == 200
    assert download.content == content
    assert download.headers["content-type"].startswith("text/plain")


def test_upload_empty_body_returns_400(client):
    response = client.put(f"/objects/{_key()}", content=b"")
    assert response.status_code == 400


def test_download_unknown_key_returns_404(client):
    response = client.get(f"/objects/{_key()}")
    assert response.status_code == 404


def test_metadata_endpoint(client):
    key = _key()
    client.put(f"/objects/{key}", content=b"data")

    response = client.get(f"/object-metadata/{key}")

    assert response.status_code == 200
    assert response.json()["object_key"] == key


def test_verify_matches_after_upload(client):
    key = _key()
    client.put(f"/objects/{key}", content=b"data")

    response = client.get(f"/object-verify/{key}")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["expected"] == body["actual"]


def test_delete_removes_object_and_metadata(client):
    key = _key()
    client.put(f"/objects/{key}", content=b"data")

    delete_response = client.delete(f"/objects/{key}")
    assert delete_response.status_code == 204

    assert client.get(f"/objects/{key}").status_code == 404
    assert client.get(f"/object-metadata/{key}").status_code == 404


def test_delete_unknown_key_returns_404(client):
    response = client.delete(f"/objects/{_key()}")
    assert response.status_code == 404


def test_overwrite_updates_metadata(client):
    key = _key()
    client.put(f"/objects/{key}", content=b"first")
    second = client.put(f"/objects/{key}", content=b"second-longer")

    assert second.json()["size_bytes"] == len(b"second-longer")
    download = client.get(f"/objects/{key}")
    assert download.content == b"second-longer"


def test_copies_endpoint_lists_single_primary_copy_without_redundancy(client):
    key = _key()
    client.put(f"/objects/{key}", content=b"data")

    response = client.get(f"/objects/{key}/copies")

    assert response.status_code == 200
    copies = response.json()
    assert len(copies) == 1
    assert copies[0]["backend_id"] == "local"
    assert copies[0]["status"] == "ok"


def test_verify_all_matches_single_backend(client):
    key = _key()
    client.put(f"/objects/{key}", content=b"data")

    response = client.get(f"/object-verify/{key}/all")

    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["backend_id"] == "local"
    assert entries[0]["ok"] is True


def test_process_pending_is_noop_without_redundancy(client):
    key = _key()
    client.put(f"/objects/{key}", content=b"data")

    response = client.post("/replication/process-pending")

    assert response.status_code == 200
    assert response.json() == {
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "permanently_failed": 0,
    }


def test_get_guard_config_returns_default(client):
    response = client.get("/guard-config")
    assert response.status_code == 200
    assert response.json()["allow_degraded_start"] is False


def test_put_guard_config_updates_and_persists(client):
    put_response = client.put("/guard-config", json={"allow_degraded_start": True})
    assert put_response.status_code == 200
    assert put_response.json()["allow_degraded_start"] is True

    get_response = client.get("/guard-config")
    assert get_response.json()["allow_degraded_start"] is True


def test_guard_status_shows_verified_identity_after_startup(client):
    """Der Lifespan-Wächter (P5b-S6) läuft bei jedem `TestClient`-Start und
    prägt/bestätigt die Geräte-ID des einzigen konfigurierten Ziels - direkt
    nach dem Start muss `/guard-status` das bereits widerspiegeln, ganz ohne
    dass zuvor ein Objekt hoch-/heruntergeladen wurde."""
    response = client.get("/guard-status")

    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["target_id"] == "local"
    assert entries[0]["device_id"] is not None
    assert entries[0]["verified_at"] is not None
    assert entries[0]["pending_copies"] == 0


def test_reidentify_unknown_target_returns_404(client):
    response = client.post("/guard-status/does-not-exist/reidentify")
    assert response.status_code == 404


def test_reidentify_writes_new_marker_when_device_is_blank(client):
    """Simuliert einen beabsichtigten Datenträger-Wechsel (3.6, P5c-S2): das
    neue Gerät ist leer - die Marker-Datei wird direkt auf dem Dateisystem
    entfernt (dasselbe `local`-Ziel, das die Testinfrastruktur konfiguriert,
    siehe conftest.py `DMS_TARGETS`), `POST .../reidentify` muss trotzdem
    eine neue Geräte-ID prägen statt zu scheitern."""
    marker_path = Path("/tmp/dms-storage-pytest") / "__dms_storage_identity__"
    old_device_id = client.get("/guard-status").json()[0]["device_id"]
    marker_path.unlink(missing_ok=True)

    response = client.post("/guard-status/local/reidentify")

    assert response.status_code == 200
    body = response.json()
    assert body["target_id"] == "local"
    assert body["device_id"] is not None
    assert body["device_id"] != old_device_id
    assert marker_path.exists()

    status_after = client.get("/guard-status").json()[0]
    assert status_after["device_id"] == body["device_id"]


def test_reidentify_resets_existing_copies_to_pending(client):
    """`reset_copies_for_backend` setzt *alle* Kopien des Ziels zurück, nicht
    nur die dieses Tests - `test_api.py` truncatet die Tabellen (anders als
    `test_repository.py`/`test_identity_guard.py`) nicht zwischen einzelnen
    Tests, daher wird hier nur das eigene, frisch hochgeladene Objekt
    geprüft statt der absoluten `pending_copies`-Gesamtzahl."""
    key = _key()
    client.put(f"/objects/{key}", content=b"payload before device swap")
    assert client.get(f"/objects/{key}/copies").json()[0]["status"] == "ok"

    response = client.post("/guard-status/local/reidentify")
    assert response.status_code == 200
    assert response.json()["pending_copies"] >= 1

    copies = client.get(f"/objects/{key}/copies").json()
    assert copies[0]["status"] == "pending"
