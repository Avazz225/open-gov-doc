import os

import httpx
import pytest
from document_service.main import app
from fastapi.testclient import TestClient

STORAGE_SERVICE_URL = os.environ.get("TEST_STORAGE_SERVICE_URL", "http://localhost:8005")

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


def test_download_content_returns_404_instead_of_crashing_if_object_missing(client):
    """Regressionstest: eine Inkonsistenz zwischen Document Service (kennt die
    Version) und Storage Service (Objekt fehlt, z. B. weil dessen Metadaten-
    Zeile verloren ging) darf nicht zu einem unbehandelten 500 führen (siehe
    storage_client.ObjectNotFoundError)."""
    body = upload(client, content=b"wird gleich verwaist").json()
    document_id = body["id"]
    checksum = client.get(f"/documents/{document_id}/versions/1").json()["checksum_sha256"]

    delete_response = httpx.delete(
        f"{STORAGE_SERVICE_URL}/objects/documents/{document_id}/{checksum}"
    )
    assert delete_response.status_code == 204

    response = client.get(f"/documents/{document_id}/content")
    assert response.status_code == 404

    response_by_version = client.get(f"/documents/{document_id}/versions/1/content")
    assert response_by_version.status_code == 404


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


def test_create_document_discards_client_supplied_kennzeichen(client):
    response = upload(client, attributes='{"Kennzeichen": "FAKE-001"}')
    assert response.status_code == 201
    assert "Kennzeichen" not in response.json()["attributes"]


def test_update_kennzeichen_without_admin_role_returns_403(client):
    body = upload(client, attributes='{"foo": "bar"}').json()
    document_id = body["id"]

    response = client.patch(
        f"/documents/{document_id}",
        json={"attributes": {"foo": "bar", "Kennzeichen": "2026-001"}},
    )
    assert response.status_code == 403


def test_update_kennzeichen_with_admin_role_succeeds(client):
    body = upload(client, attributes='{"foo": "bar"}').json()
    document_id = body["id"]

    response = client.patch(
        f"/documents/{document_id}",
        json={"attributes": {"foo": "bar", "Kennzeichen": "2026-001"}},
        headers={"X-DMS-Roles": "dms-admin"},
    )
    assert response.status_code == 200
    assert response.json()["attributes"]["Kennzeichen"] == "2026-001"


def test_update_kennzeichen_with_other_roles_still_returns_403(client):
    body = upload(client, attributes='{"foo": "bar"}').json()
    document_id = body["id"]

    response = client.patch(
        f"/documents/{document_id}",
        json={"attributes": {"foo": "bar", "Kennzeichen": "2026-001"}},
        headers={"X-DMS-Roles": "some-other-role,another-role"},
    )
    assert response.status_code == 403


def test_update_attributes_without_touching_kennzeichen_needs_no_role(client):
    body = upload(client, attributes='{"foo": "bar"}').json()
    document_id = body["id"]

    response = client.patch(f"/documents/{document_id}", json={"attributes": {"foo": "baz"}})
    assert response.status_code == 200
    assert response.json()["attributes"] == {"foo": "baz"}


def test_removing_existing_kennzeichen_via_attribute_replace_needs_admin_role(client):
    body = upload(client, attributes='{"foo": "bar"}').json()
    document_id = body["id"]
    client.patch(
        f"/documents/{document_id}",
        json={"attributes": {"Kennzeichen": "2026-001"}},
        headers={"X-DMS-Roles": "dms-admin"},
    )

    response = client.patch(f"/documents/{document_id}", json={"attributes": {"foo": "bar"}})
    assert response.status_code == 403


def test_upload_content_type_is_sniffed_not_trusted(client):
    """P5d-S1: der vom Browser gesendete Header wird nicht mehr übernommen -
    hier klar sichtbar, da `upload()` Klartext-Inhalt als "application/pdf"
    deklariert, aber das tatsächliche Byte-Sniffing "text/plain" ermittelt."""
    body = upload(client, content=b"Hallo Welt").json()
    version = client.get(f"/documents/{body['id']}/versions/1").json()
    assert version["content_type"] == "text/plain"


def test_checkin_content_type_is_sniffed_not_trusted(client):
    body = upload(client, content=b"v1").json()
    document_id = body["id"]

    client.post(
        f"/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "alice"},
        files={"file": ("daten.json", b'{"a": 1}', "application/pdf")},
    )

    version = client.get(f"/documents/{document_id}/versions/2").json()
    assert version["content_type"] == "application/json"


def test_upload_rejects_content_type_not_on_whitelist(client):
    config_response = client.put(
        "/upload-config", json={"allowed_content_types": ["application/pdf"]}
    )
    assert config_response.status_code == 200

    response = upload(client, content=b"Hallo Welt")  # sniffed als text/plain

    assert response.status_code == 400


def test_upload_allows_content_type_on_whitelist(client):
    client.put("/upload-config", json={"allowed_content_types": ["text/plain"]})

    response = upload(client, content=b"Hallo Welt")

    assert response.status_code == 201


def test_upload_config_empty_whitelist_means_no_restriction(client):
    response = client.get("/upload-config")
    assert response.status_code == 200
    assert response.json()["allowed_content_types"] == []

    response = upload(client, content=b"Hallo Welt")
    assert response.status_code == 201


def test_put_upload_config_persists(client):
    put_response = client.put(
        "/upload-config", json={"allowed_content_types": ["application/pdf", "text/plain"]}
    )
    assert put_response.status_code == 200
    assert put_response.json()["allowed_content_types"] == ["application/pdf", "text/plain"]

    get_response = client.get("/upload-config")
    assert get_response.json()["allowed_content_types"] == ["application/pdf", "text/plain"]


def test_delete_document(client):
    body = upload(client).json()
    document_id = body["id"]

    response = client.request("DELETE", f"/documents/{document_id}?deleted_by=admin")
    assert response.status_code == 200
    assert response.json()["deleted_at"] is not None


def test_create_document_with_derived_from_fields_creates_independent_document(client):
    """Bearbeitungskopie (2.3, P6-S3, z. B. Schwaerzung fuer die Akteneinsicht):
    kein neues Endpunkt noetig - die Herkunftsfelder laufen ueber die normale
    Upload-Pipeline und das Ergebnis ist ein ganz eigenstaendiges Dokument."""
    original = upload(client, title="Original").json()

    copy_response = upload(
        client,
        title="Schwaerzung",
        content=b"geschwaerzter Inhalt",
        derived_from_document_id=original["id"],
        derived_from_version_number=1,
        originating_case_id="case-123",
    )
    assert copy_response.status_code == 201
    copy = copy_response.json()
    assert copy["id"] != original["id"]
    assert copy["derived_from_document_id"] == original["id"]
    assert copy["derived_from_version_number"] == 1
    assert copy["originating_case_id"] == "case-123"

    # Unabhaengige Versionierung/Auditierung - eigener current_version_number,
    # das Original bleibt unveraendert.
    assert copy["current_version_number"] == 1
    original_still_unchanged = client.get(f"/documents/{original['id']}").json()
    assert original_still_unchanged["derived_from_document_id"] is None


def test_create_document_without_derived_fields_has_null_origin(client):
    body = upload(client).json()
    assert body["derived_from_document_id"] is None
    assert body["derived_from_version_number"] is None
    assert body["originating_case_id"] is None


def test_create_document_derived_from_without_version_number_returns_400(client):
    original = upload(client).json()
    response = upload(client, derived_from_document_id=original["id"])
    assert response.status_code == 400


def test_create_document_derived_from_unknown_version_returns_400(client):
    original = upload(client).json()
    response = upload(
        client, derived_from_document_id=original["id"], derived_from_version_number=99
    )
    assert response.status_code == 400


def test_create_document_derived_from_unknown_document_returns_400(client):
    response = upload(
        client, derived_from_document_id="does-not-exist", derived_from_version_number=1
    )
    assert response.status_code == 400
