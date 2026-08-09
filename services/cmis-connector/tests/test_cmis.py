"""CMIS-1.1-Browser-Binding-Connector (3.3, P12-S4). Läuft wie
`webdav-connector`/`migration-service` gegen den echten, per docker-compose
laufenden Container (kein In-Prozess-`TestClient`, kein Mocking der
Nachbar-Services)."""

import os
import uuid

import httpx

# Eigenständig gelesen statt aus conftest importiert (siehe dortiger
# Kommentar in anderen Services: `from conftest import x` ist mit
# `--import-mode=importlib` über Testmodule hinweg nicht zuverlässig).
CMIS_CONNECTOR_URL = os.environ.get("TEST_CMIS_CONNECTOR_URL", "http://localhost:8030")
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
REPOSITORY_ID = "default"


def _root_url() -> str:
    return f"{CMIS_CONNECTOR_URL}/browser/{REPOSITORY_ID}/root"


def _get(auth, **params) -> httpx.Response:
    return httpx.get(_root_url(), auth=auth, params=params, timeout=10.0)


def _post(auth, **data) -> httpx.Response:
    return httpx.post(_root_url(), auth=auth, data=data, timeout=10.0)


def _post_with_file(auth, content: bytes, filename: str = "datei.txt", **data) -> httpx.Response:
    return httpx.post(
        _root_url(),
        auth=auth,
        data=data,
        files={"content": (filename, content, "text/plain")},
        timeout=10.0,
    )


def _create_folder(auth) -> dict:
    response = _post(
        auth,
        cmisaction="createFolder",
        **{"propertyId[0]": "cmis:name", "propertyValue[0]": f"cmis-ordner-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["succinctProperties"]


def _create_document(auth, *, folder_id: str | None = None, content: bytes = b"Inhalt") -> dict:
    params = {"objectId": folder_id} if folder_id else {}
    response = httpx.post(
        _root_url(),
        auth=auth,
        params=params,
        data={
            "cmisaction": "createDocument",
            "propertyId[0]": "cmis:name",
            "propertyValue[0]": f"dokument-{uuid.uuid4().hex[:8]}.txt",
        },
        files={"content": ("dokument.txt", content, "text/plain")},
        timeout=10.0,
    )
    assert response.status_code == 201, response.text
    return response.json()["succinctProperties"]


def test_healthz_needs_no_authentication():
    response = httpx.get(f"{CMIS_CONNECTOR_URL}/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "cmis-connector"


def test_root_without_credentials_returns_401_with_challenge():
    response = httpx.get(_root_url())
    assert response.status_code == 401
    assert "Basic" in response.headers["WWW-Authenticate"]


def test_wrong_credentials_are_rejected():
    response = _get(("nobody", "wrong-password"))
    assert response.status_code == 403


def test_get_repositories_returns_default_repository(real_user):
    response = httpx.get(f"{CMIS_CONNECTOR_URL}/browser", auth=real_user)
    assert response.status_code == 200
    body = response.json()
    assert REPOSITORY_ID in body
    assert body[REPOSITORY_ID]["rootFolderId"] == "root"
    assert body[REPOSITORY_ID]["cmisVersionSupported"] == "1.1"


def test_root_children_lists_a_freshly_created_folder(real_user):
    folder = _create_folder(real_user)

    response = _get(real_user, cmisselector="children")

    assert response.status_code == 200
    body = response.json()
    names = {o["object"]["succinctProperties"]["cmis:name"] for o in body["objects"]}
    assert folder["cmis:name"] in names


def test_get_object_by_id_returns_folder_properties(real_user):
    folder = _create_folder(real_user)

    response = _get(real_user, objectId=folder["cmis:objectId"], cmisselector="object")

    assert response.status_code == 200
    props = response.json()["succinctProperties"]
    assert props["cmis:objectId"] == folder["cmis:objectId"]
    assert props["cmis:baseTypeId"] == "cmis:folder"
    assert props["cmis:path"] == f"/{folder['cmis:name']}"


def test_create_document_and_read_content(real_user):
    document = _create_document(real_user, content=b"Hallo CMIS")

    response = _get(real_user, objectId=document["cmis:objectId"], cmisselector="content")

    assert response.status_code == 200
    assert response.content == b"Hallo CMIS"
    assert document["cmis:baseTypeId"] == "cmis:document"
    assert document["cmis:versionLabel"] == "1"


def test_content_is_default_selector_for_documents(real_user):
    document = _create_document(real_user, content=b"Default-Selector")

    response = _get(real_user, objectId=document["cmis:objectId"])

    assert response.status_code == 200
    assert response.content == b"Default-Selector"


def test_update_renames_document(real_user):
    document = _create_document(real_user)
    new_name = f"umbenannt-{uuid.uuid4().hex[:8]}.txt"

    response = _post(
        real_user,
        cmisaction="update",
        objectId=document["cmis:objectId"],
        **{"propertyId[0]": "cmis:name", "propertyValue[0]": new_name},
    )

    assert response.status_code == 200
    assert response.json()["succinctProperties"]["cmis:name"] == new_name


def test_move_moves_document_to_target_folder(real_user):
    target = _create_folder(real_user)
    document = _create_document(real_user)

    response = _post(
        real_user,
        cmisaction="move",
        objectId=document["cmis:objectId"],
        targetFolderId=target["cmis:objectId"],
    )

    assert response.status_code == 201
    children = _get(real_user, objectId=target["cmis:objectId"], cmisselector="children").json()
    ids = {o["object"]["succinctProperties"]["cmis:objectId"] for o in children["objects"]}
    assert document["cmis:objectId"] in ids


def test_set_content_creates_new_version(real_user):
    document = _create_document(real_user, content=b"Version 1")

    response = _post_with_file(
        real_user, b"Version 2", cmisaction="setContent", objectId=document["cmis:objectId"]
    )

    assert response.status_code == 201
    updated = response.json()["succinctProperties"]
    assert updated["cmis:versionLabel"] == "2"
    content = _get(real_user, objectId=document["cmis:objectId"], cmisselector="content")
    assert content.content == b"Version 2"


def test_checkout_then_checkin_updates_content_and_releases_lock(real_user):
    document = _create_document(real_user, content=b"Original")

    checkout = _post(real_user, cmisaction="checkOut", objectId=document["cmis:objectId"])
    assert checkout.status_code == 201
    assert checkout.json()["succinctProperties"]["cmis:isVersionSeriesCheckedOut"] is True

    checkin = _post_with_file(
        real_user,
        b"Eingecheckter Inhalt",
        cmisaction="checkIn",
        objectId=document["cmis:objectId"],
        checkinComment="Testkommentar",
    )
    assert checkin.status_code == 201
    props = checkin.json()["succinctProperties"]
    assert props["cmis:isVersionSeriesCheckedOut"] is False
    assert props["cmis:versionLabel"] == "2"

    content = _get(real_user, objectId=document["cmis:objectId"], cmisselector="content")
    assert content.content == b"Eingecheckter Inhalt"


def test_checkout_conflict_returns_updateconflict(real_user, second_real_user):
    document = _create_document(real_user)
    first = _post(real_user, cmisaction="checkOut", objectId=document["cmis:objectId"])
    assert first.status_code == 201

    # `document-service`s Sperrprüfung vergleicht `locked_by`, nicht
    # `session_id` - derselbe Akteur kann seine eigene Sperre jederzeit
    # erneut "erwerben" (idempotent), ein echter Konflikt braucht daher
    # einen ZWEITEN Akteur.
    second = _post(second_real_user, cmisaction="checkOut", objectId=document["cmis:objectId"])

    assert second.status_code == 409
    assert second.json()["exception"] == "updateConflict"


def test_cancel_checkout_releases_lock_without_changing_content(real_user):
    document = _create_document(real_user, content=b"Unveraendert")
    _post(real_user, cmisaction="checkOut", objectId=document["cmis:objectId"])

    response = _post(real_user, cmisaction="cancelCheckOut", objectId=document["cmis:objectId"])

    assert response.status_code == 200
    content = _get(real_user, objectId=document["cmis:objectId"], cmisselector="content")
    assert content.content == b"Unveraendert"
    # Sperre wurde tatsaechlich aufgehoben - ein erneutes Checkout muss wieder moeglich sein.
    second_checkout = _post(real_user, cmisaction="checkOut", objectId=document["cmis:objectId"])
    assert second_checkout.status_code == 201


def test_delete_document(real_user):
    document = _create_document(real_user)

    response = _post(real_user, cmisaction="delete", objectId=document["cmis:objectId"])

    assert response.status_code == 200
    detail = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents/{document['cmis:objectId']}")
    assert detail.json()["deleted_at"] is not None


def test_delete_nonempty_folder_returns_constraint(real_user):
    folder = _create_folder(real_user)
    _create_document(real_user, folder_id=folder["cmis:objectId"])

    response = _post(real_user, cmisaction="delete", objectId=folder["cmis:objectId"])

    assert response.status_code == 409
    assert response.json()["exception"] == "constraint"


def test_delete_tree_cascades_documents_and_subfolders(real_user):
    folder = _create_folder(real_user)
    document = _create_document(real_user, folder_id=folder["cmis:objectId"])
    sub_response = _post(
        real_user,
        cmisaction="createFolder",
        objectId=folder["cmis:objectId"],
        **{"propertyId[0]": "cmis:name", "propertyValue[0]": "unterordner"},
    )
    assert sub_response.status_code == 201

    response = _post(real_user, cmisaction="deleteTree", objectId=folder["cmis:objectId"])

    assert response.status_code == 200
    assert (
        _get(real_user, objectId=folder["cmis:objectId"], cmisselector="object").status_code == 404
    )
    detail = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents/{document['cmis:objectId']}")
    assert detail.json()["deleted_at"] is not None
