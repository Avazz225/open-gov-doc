import os
import uuid
from io import BytesIO

import httpx
import pytest
from webdav4.client import Client
from webdav4.client import HTTPError as WebdavHTTPError

WEBDAV_CONNECTOR_URL = os.environ.get("TEST_WEBDAV_CONNECTOR_URL", "http://localhost:8027")
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
FOLDER_SERVICE_URL = os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")


def _dav_client(user: tuple[str, str]) -> Client:
    return Client(f"{WEBDAV_CONNECTOR_URL}/webdav", auth=user)


def _create_folder(*, parent_id: str = "root", name: str | None = None) -> dict:
    response = httpx.post(
        f"{FOLDER_SERVICE_URL}/folders",
        json={
            "name": name or f"webdav-ordner-{uuid.uuid4().hex[:8]}",
            "parent_id": parent_id,
            "created_by": "webdav-tests",
        },
    )
    response.raise_for_status()
    return response.json()


def _get_document(document_id: str) -> dict:
    response = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents/{document_id}")
    response.raise_for_status()
    return response.json()


def _grant_document_write(principal_id: str) -> None:
    """Office-Direktbearbeitung (Post-Roadmap-Feature) - `POST .../webdav-
    edit-tokens` verlangt (anders als der übrige, hier ungeprüfte WebDAV-
    Basic-Auth-Fluss) echte `document.write`-Berechtigung, gleiches Muster
    wie document-service's eigene Tests. `POST /role-assignments` liefert
    seit P17-S3 immer `201`, auch bei `status="pending_approval"` (falls
    `permission.role_assignment.create` auf dieser Installation echt
    Vier-Augen-pflichtig ist, z. B. durch ein zuvor angewendetes
    Konfigurationspaket) - `raise_for_status()` allein reicht daher nicht
    mehr. Die Pflicht wird nur für die Dauer dieses Grants ausgesetzt und
    danach zurückgesetzt, nicht dauerhaft überschrieben."""
    config = httpx.get(
        f"{PERMISSION_SERVICE_URL}/approval-config/permission.role_assignment.create",
        timeout=30.0,
    )
    originally_required = config.status_code == 200 and config.json()["requires_approval"]
    if originally_required:
        httpx.put(
            f"{PERMISSION_SERVICE_URL}/approval-config/permission.role_assignment.create",
            json={"requires_approval": False},
            timeout=30.0,
        )
    try:
        role = httpx.post(
            f"{PERMISSION_SERVICE_URL}/roles",
            json={
                "name": f"webdav-connector-edit-token-test-role-{uuid.uuid4().hex[:8]}",
                "permissions": ["document.write"],
            },
            timeout=30.0,
        )
        role.raise_for_status()
        assignment = httpx.post(
            f"{PERMISSION_SERVICE_URL}/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": principal_id,
                "role_id": role.json()["id"],
                "resource_id": "root",
            },
            timeout=30.0,
        )
        assignment.raise_for_status()
        assert assignment.json()["status"] == "created", (
            f"Rollenzuweisung wurde nicht sofort wirksam: {assignment.json()}"
        )
    finally:
        if originally_required:
            httpx.put(
                f"{PERMISSION_SERVICE_URL}/approval-config/permission.role_assignment.create",
                json={"requires_approval": True},
                timeout=30.0,
            )


def _create_webdav_edit_token(document_id: str, principal_id: str) -> str:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents/{document_id}/webdav-edit-tokens",
        headers={"X-DMS-Principal": principal_id},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["token"]


def test_healthz_needs_no_authentication():
    response = httpx.get(f"{WEBDAV_CONNECTOR_URL}/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "webdav-connector"


def test_webdav_root_requires_authentication():
    response = httpx.request("PROPFIND", f"{WEBDAV_CONNECTOR_URL}/webdav/", headers={"Depth": "0"})
    assert response.status_code == 401


def test_wrong_credentials_are_rejected():
    client = Client(f"{WEBDAV_CONNECTOR_URL}/webdav", auth=("nobody", "wrong-password"))
    with pytest.raises(WebdavHTTPError) as exc_info:
        client.ls("/")
    assert exc_info.value.status_code == 401


def test_root_listing_shows_a_freshly_created_folder(real_user):
    folder = _create_folder(name=f"sichtbar-{uuid.uuid4().hex[:8]}")

    entries = _dav_client(real_user).ls("/", detail=True)

    names = {os.path.basename(entry["name"].rstrip("/")) for entry in entries}
    assert folder["name"] in names


def test_put_creates_a_new_document(real_user):
    filename = f"neu-{uuid.uuid4().hex[:8]}.txt"

    _dav_client(real_user).upload_fileobj(BytesIO(b"Hallo WebDAV"), f"/{filename}")

    entries = _dav_client(real_user).ls("/", detail=True)
    match = next(e for e in entries if os.path.basename(e["name"].rstrip("/")) == filename)
    assert match["content_length"] == len(b"Hallo WebDAV")


def test_get_returns_the_same_content_that_was_put(real_user):
    filename = f"inhalt-{uuid.uuid4().hex[:8]}.txt"
    content = b"WebDAV-Roundtrip-Inhalt"
    client = _dav_client(real_user)
    client.upload_fileobj(BytesIO(content), f"/{filename}")

    buffer = BytesIO()
    client.download_fileobj(f"/{filename}", buffer)

    assert buffer.getvalue() == content


def test_put_on_existing_path_checks_in_a_new_version(real_user):
    filename = f"version-{uuid.uuid4().hex[:8]}.txt"
    client = _dav_client(real_user)
    client.upload_fileobj(BytesIO(b"Version 1"), f"/{filename}")
    client.upload_fileobj(BytesIO(b"Version 2"), f"/{filename}", overwrite=True)

    buffer = BytesIO()
    client.download_fileobj(f"/{filename}", buffer)
    assert buffer.getvalue() == b"Version 2"

    entries = client.ls("/", detail=True)
    match = next(e for e in entries if os.path.basename(e["name"].rstrip("/")) == filename)
    assert match["content_length"] == len(b"Version 2")


def test_mkcol_creates_a_folder_visible_to_folder_service(real_user):
    name = f"webdav-mkcol-{uuid.uuid4().hex[:8]}"

    _dav_client(real_user).mkdir(f"/{name}")

    response = httpx.get(f"{FOLDER_SERVICE_URL}/folders/root/children")
    response.raise_for_status()
    assert any(f["name"] == name for f in response.json())


def test_move_renames_a_document(real_user):
    old_name = f"alt-{uuid.uuid4().hex[:8]}.txt"
    new_name = f"neu-{uuid.uuid4().hex[:8]}.txt"
    client = _dav_client(real_user)
    client.upload_fileobj(BytesIO(b"Inhalt"), f"/{old_name}")

    client.move(f"/{old_name}", f"/{new_name}")

    entries = {os.path.basename(e["name"].rstrip("/")) for e in client.ls("/", detail=True)}
    assert new_name in entries
    assert old_name not in entries


def test_move_between_folders_updates_document_service_folder_id(real_user):
    target = _create_folder(name=f"ziel-{uuid.uuid4().hex[:8]}")
    filename = f"verschoben-{uuid.uuid4().hex[:8]}.txt"
    client = _dav_client(real_user)
    client.upload_fileobj(BytesIO(b"Inhalt"), f"/{filename}")
    matching = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents", params={"folder_id": "root"}).json()
    document_id = next(d["id"] for d in matching if d["title"] == filename)

    client.move(f"/{filename}", f"/{target['name']}/{filename}")

    assert _get_document(document_id)["folder_id"] == target["id"]


def test_delete_soft_deletes_the_document(real_user):
    filename = f"loeschen-{uuid.uuid4().hex[:8]}.txt"
    client = _dav_client(real_user)
    client.upload_fileobj(BytesIO(b"weg damit"), f"/{filename}")
    matching = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents", params={"folder_id": "root"}).json()
    document_id = next(d["id"] for d in matching if d["title"] == filename)

    client.remove(f"/{filename}")

    assert _get_document(document_id)["deleted_at"] is not None


def test_lock_conflict_is_reported_as_locked(real_user):
    filename = f"gesperrt-{uuid.uuid4().hex[:8]}.txt"
    client = _dav_client(real_user)
    client.upload_fileobj(BytesIO(b"initial"), f"/{filename}")
    matching = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents", params={"folder_id": "root"}).json()
    document_id = next(d["id"] for d in matching if d["title"] == filename)

    lock_response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents/{document_id}/lock",
        json={"locked_by": "andere-anwendung", "session_id": "fremd-session"},
    )
    lock_response.raise_for_status()
    try:
        with pytest.raises(WebdavHTTPError) as exc_info:
            client.upload_fileobj(BytesIO(b"ueberschreiben"), f"/{filename}", overwrite=True)
        assert exc_info.value.status_code == 423
    finally:
        httpx.request(
            "DELETE",
            f"{DOCUMENT_SERVICE_URL}/documents/{document_id}/lock",
            json={"released_by": "andere-anwendung"},
        )


# --- Office-Direktbearbeitung (Post-Roadmap-Feature, WebDAV-Edit-Token) -----


def test_by_id_get_with_edit_token_returns_same_content_as_path_based_access(real_user):
    filename = f"by-id-{uuid.uuid4().hex[:8]}.txt"
    content = b"Inhalt fuer ID-basierten Zugriff"
    path_client = _dav_client(real_user)
    path_client.upload_fileobj(BytesIO(content), f"/{filename}")
    matching = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents", params={"folder_id": "root"}).json()
    document_id = next(d["id"] for d in matching if d["title"] == filename)

    principal = f"webdav-edit-token-test-{uuid.uuid4().hex[:8]}"
    _grant_document_write(principal)
    token = _create_webdav_edit_token(document_id, principal)

    token_client = Client(f"{WEBDAV_CONNECTOR_URL}/webdav", auth=(token, ""))
    buffer = BytesIO()
    token_client.download_fileobj(f"/by-id/{document_id}.txt", buffer)

    assert buffer.getvalue() == content


def test_by_id_put_with_edit_token_checks_in_a_new_version(real_user):
    filename = f"by-id-put-{uuid.uuid4().hex[:8]}.txt"
    path_client = _dav_client(real_user)
    path_client.upload_fileobj(BytesIO(b"Version 1"), f"/{filename}")
    matching = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents", params={"folder_id": "root"}).json()
    document_id = next(d["id"] for d in matching if d["title"] == filename)

    principal = f"webdav-edit-token-test-{uuid.uuid4().hex[:8]}"
    _grant_document_write(principal)
    token = _create_webdav_edit_token(document_id, principal)

    token_client = Client(f"{WEBDAV_CONNECTOR_URL}/webdav", auth=(token, ""))
    token_client.upload_fileobj(
        BytesIO(b"Version 2 via Edit-Token"), f"/by-id/{document_id}.txt", overwrite=True
    )

    buffer = BytesIO()
    path_client.download_fileobj(f"/{filename}", buffer)
    assert buffer.getvalue() == b"Version 2 via Edit-Token"
    # Check-in muss die aufgeloeste Identitaet als Ersteller verwenden, nicht
    # das rohe Token selbst.
    assert _get_document(document_id)["created_by"] != token


def test_by_id_access_with_expired_or_revoked_token_is_rejected(real_user):
    filename = f"by-id-revoked-{uuid.uuid4().hex[:8]}.txt"
    path_client = _dav_client(real_user)
    path_client.upload_fileobj(BytesIO(b"Inhalt"), f"/{filename}")
    matching = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents", params={"folder_id": "root"}).json()
    document_id = next(d["id"] for d in matching if d["title"] == filename)

    principal = f"webdav-edit-token-test-{uuid.uuid4().hex[:8]}"
    _grant_document_write(principal)
    token = _create_webdav_edit_token(document_id, principal)
    httpx.delete(
        f"{DOCUMENT_SERVICE_URL}/webdav-edit-tokens/{token}",
        headers={"X-DMS-Principal": principal},
    )

    token_client = Client(f"{WEBDAV_CONNECTOR_URL}/webdav", auth=(token, ""))
    with pytest.raises(WebdavHTTPError) as exc_info:
        token_client.ls(f"/by-id/{document_id}.txt")
    assert exc_info.value.status_code == 401


def test_by_id_access_with_unknown_token_is_rejected():
    document_id = str(uuid.uuid4())
    token_client = Client(f"{WEBDAV_CONNECTOR_URL}/webdav", auth=("not-a-real-token", ""))
    with pytest.raises(WebdavHTTPError) as exc_info:
        token_client.ls(f"/by-id/{document_id}.txt")
    assert exc_info.value.status_code == 401
