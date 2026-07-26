import hashlib
import uuid

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
    assert response.json()["backend"] == "local"


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
