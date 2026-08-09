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
