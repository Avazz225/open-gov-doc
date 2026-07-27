import pytest
from document_service.main import app
from fastapi.testclient import TestClient

# Standardisierte EICAR-Testdatei-Signatur (https://www.eicar.org/) - von
# echten Antivirus-Produkten zu Integrationstestzwecken erkannt, hier zum
# Verifizieren der Virenscan-Gate-Integration (10.3, ADR 0010) verwendet.
EICAR_SIGNATURE = (
    r"X5O!P%@AP[4\PZX54(P^)7CC)7}$" "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
).encode("ascii")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def upload(client, *, content=b"Hallo Welt", title="Vertrag", created_by="alice", **extra):
    data = {"title": title, "created_by": created_by, **extra}
    files = {"file": ("vertrag.pdf", content, "application/pdf")}
    return client.post("/documents", data=data, files=files)


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "document-service"


def test_create_and_get_document(client):
    response = upload(client)
    assert response.status_code == 201
    body = response.json()
    assert body["current_version_number"] == 1
    assert body["title"] == "Vertrag"

    get_response = client.get(f"/documents/{body['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == body["id"]


def test_download_current_content_roundtrips_bytes(client):
    body = upload(client, content=b"Original-Inhalt").json()

    response = client.get(f"/documents/{body['id']}/content")
    assert response.status_code == 200
    assert response.content == b"Original-Inhalt"


def test_get_unknown_document_returns_404(client):
    response = client.get("/documents/does-not-exist")
    assert response.status_code == 404


def test_list_documents_by_folder(client):
    in_folder = upload(client, title="Im Ordner", folder_id="root").json()
    upload(client, title="Ohne Ordner")

    response = client.get("/documents", params={"folder_id": "root"})

    assert response.status_code == 200
    ids = [d["id"] for d in response.json()]
    assert in_folder["id"] in ids
    assert all(d["folder_id"] == "root" for d in response.json())


def test_list_documents_excludes_deleted(client):
    body = upload(client, folder_id="root").json()
    client.delete(f"/documents/{body['id']}", params={"deleted_by": "alice"})

    response = client.get("/documents", params={"folder_id": "root"})

    assert body["id"] not in [d["id"] for d in response.json()]


def test_list_documents_unknown_folder_returns_empty(client):
    response = client.get("/documents", params={"folder_id": "does-not-exist"})
    assert response.status_code == 200
    assert response.json() == []


def test_create_document_rejects_infected_upload(client):
    response = upload(client, content=EICAR_SIGNATURE)

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "virus_detected"
    assert response.json()["detail"]["threat_name"] == "Eicar-Test-Signature"


def test_checkin_rejects_infected_version_without_creating_it(client):
    body = upload(client, content=b"v1").json()
    document_id = body["id"]

    response = client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "alice"},
        files={"file": ("infiziert.pdf", EICAR_SIGNATURE, "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "virus_detected"

    versions = client.get(f"/documents/{document_id}/versions").json()
    assert len(versions) == 1

    download = client.get(f"/documents/{document_id}/content")
    assert download.content == b"v1"


def test_checkin_normal_version_and_download(client):
    body = upload(client, content=b"v1").json()
    document_id = body["id"]

    response = client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "alice"},
        files={"file": ("vertrag.pdf", b"v2", "application/pdf")},
    )
    assert response.status_code == 201
    result = response.json()
    assert result["is_conflict"] is False
    assert result["version"]["version_number"] == 2

    download = client.get(f"/documents/{document_id}/content")
    assert download.content == b"v2"


def test_checkin_stale_base_produces_conflict_copy_without_moving_current(client):
    body = upload(client, content=b"v1").json()
    document_id = body["id"]

    client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "bob"},
        files={"file": ("vertrag.pdf", b"bobs-version", "application/pdf")},
    )

    conflict_response = client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "alice"},
        files={"file": ("vertrag.pdf", b"alice-stale-version", "application/pdf")},
    )
    assert conflict_response.status_code == 201
    conflict_result = conflict_response.json()
    assert conflict_result["is_conflict"] is True
    assert "_conflict_alice_" in conflict_result["version"]["filename"]

    current = client.get(f"/documents/{document_id}/content")
    assert current.content == b"bobs-version"


def test_lock_conflict_returns_409(client):
    body = upload(client).json()
    document_id = body["id"]

    first = client.post(
        f"/documents/{document_id}/lock",
        json={"locked_by": "alice", "session_id": "s1"},
    )
    assert first.status_code == 201

    second = client.post(
        f"/documents/{document_id}/lock",
        json={"locked_by": "bob", "session_id": "s2"},
    )
    assert second.status_code == 409


def test_release_lock_wrong_holder_returns_403(client):
    body = upload(client).json()
    document_id = body["id"]
    client.post(f"/documents/{document_id}/lock", json={"locked_by": "alice", "session_id": "s1"})

    response = client.request(
        "DELETE", f"/documents/{document_id}/lock", json={"released_by": "bob"}
    )
    assert response.status_code == 403


def test_force_release_then_conflicting_checkin(client):
    body = upload(client, content=b"v1").json()
    document_id = body["id"]
    client.post(f"/documents/{document_id}/lock", json={"locked_by": "alice", "session_id": "s1"})

    force_response = client.post(
        f"/documents/{document_id}/lock/force-release",
        json={"released_by": "admin", "reason": "Mitarbeiter im Urlaub"},
    )
    assert force_response.status_code == 200
    assert force_response.json()["locked_by"] == "alice"

    # Sperre ist wirklich weg - Bob kann jetzt normal einchecken.
    bob_response = client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "bob"},
        files={"file": ("vertrag.pdf", b"bobs-version", "application/pdf")},
    )
    assert bob_response.json()["is_conflict"] is False

    # Alice versucht danach ebenfalls einzuchecken - landet als Konfliktkopie.
    alice_response = client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "alice"},
        files={"file": ("vertrag.pdf", b"alice-stale", "application/pdf")},
    )
    assert alice_response.json()["is_conflict"] is True


def test_update_document_metadata(client):
    body = upload(client, title="Alt").json()
    document_id = body["id"]

    response = client.patch(
        f"/documents/{document_id}",
        json={"title": "Neu", "attributes": {"foo": "bar"}},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["title"] == "Neu"
    assert updated["attributes"] == {"foo": "bar"}

    get_response = client.get(f"/documents/{document_id}")
    assert get_response.json()["title"] == "Neu"


def test_update_document_metadata_partial_update_keeps_title(client):
    body = upload(client, title="Bleibt").json()
    document_id = body["id"]

    response = client.patch(f"/documents/{document_id}", json={"attributes": {"foo": "bar"}})

    assert response.status_code == 200
    assert response.json()["title"] == "Bleibt"


def test_update_document_metadata_unknown_document_returns_404(client):
    response = client.patch("/documents/does-not-exist", json={"title": "x"})
    assert response.status_code == 404


def test_delete_document(client):
    body = upload(client).json()
    document_id = body["id"]

    response = client.request("DELETE", f"/documents/{document_id}?deleted_by=admin")
    assert response.status_code == 200
    assert response.json()["deleted_at"] is not None
