import os
import uuid

import httpx
import pytest
from dms_connector_sdk import (
    DmsTreeClient,
    LockConflictError,
    PathNotFoundError,
)

DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
FOLDER_SERVICE_URL = os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")


@pytest.fixture
def sdk_client():
    client = DmsTreeClient(
        document_service_base_url=DOCUMENT_SERVICE_URL,
        folder_service_base_url=FOLDER_SERVICE_URL,
    )
    try:
        yield client
    finally:
        client.close()


def _create_folder(*, parent_id: str = "root", name: str | None = None) -> dict:
    response = httpx.post(
        f"{FOLDER_SERVICE_URL}/folders",
        json={
            "name": name or f"Ordner-{uuid.uuid4().hex[:8]}",
            "parent_id": parent_id,
            "created_by": "connector-sdk-tests",
        },
    )
    response.raise_for_status()
    return response.json()


def _upload_document(
    *, folder_id: str = "root", title: str | None = None, content: bytes = b"Hallo"
) -> dict:
    resolved_title = title or f"dokument-{uuid.uuid4().hex[:8]}.txt"
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={
            "title": resolved_title,
            "created_by": "connector-sdk-tests",
            "folder_id": folder_id,
        },
        files={"file": (resolved_title, content, "text/plain")},
    )
    response.raise_for_status()
    return response.json()


def test_list_children_separates_folders_and_documents(sdk_client):
    folder = _create_folder()
    document = _upload_document(folder_id=folder["id"], title="in-ordner.txt")
    subfolder = _create_folder(parent_id=folder["id"], name="unterordner")

    folders, documents = sdk_client.list_children(folder["id"])

    assert [f.id for f in folders] == [subfolder["id"]]
    assert [d.id for d in documents] == [document["id"]]
    assert documents[0].title == "in-ordner.txt"


def test_resolve_path_walks_nested_folders_to_a_document(sdk_client):
    top = _create_folder(name=f"top-{uuid.uuid4().hex[:8]}")
    nested = _create_folder(parent_id=top["id"], name="nested")
    document = _upload_document(folder_id=nested["id"], title="ziel.txt")

    resolved = sdk_client.resolve_path(f"{top['name']}/nested/ziel.txt")

    assert resolved.id == document["id"]


def test_resolve_path_unknown_segment_raises(sdk_client):
    with pytest.raises(PathNotFoundError):
        sdk_client.resolve_path(f"nie-existent-{uuid.uuid4().hex[:8]}/x.txt")


def test_write_document_creates_then_checks_in_new_version(sdk_client):
    folder = _create_folder()

    created = sdk_client.write_document(
        folder_id=folder["id"],
        filename="datei.txt",
        content=b"Version 1",
        content_type="text/plain",
        created_by="connector-sdk-tests",
    )
    assert created.current_version_number == 1
    assert sdk_client.read_document_content(created.id) == b"Version 1"

    updated = sdk_client.write_document(
        folder_id=folder["id"],
        filename="datei.txt",
        content=b"Version 2",
        content_type="text/plain",
        created_by="connector-sdk-tests",
        existing_document_id=created.id,
        expected_base_version_number=1,
    )
    assert updated.current_version_number == 2
    assert sdk_client.read_document_content(updated.id) == b"Version 2"


def test_move_document_updates_folder_id(sdk_client):
    source_folder = _create_folder()
    target_folder = _create_folder()
    document = _upload_document(folder_id=source_folder["id"])

    moved = sdk_client.move_document(document["id"], new_folder_id=target_folder["id"])

    assert moved.folder_id == target_folder["id"]


def test_move_document_to_unknown_folder_raises(sdk_client):
    document = _upload_document()

    with pytest.raises(PathNotFoundError):
        sdk_client.move_document(document["id"], new_folder_id="unbekannt")


def test_lock_lifecycle(sdk_client):
    document = _upload_document()

    assert sdk_client.get_lock(document["id"]) is None

    lock = sdk_client.acquire_lock(document["id"], locked_by="alice", session_id="session-1")
    assert lock.locked_by == "alice"
    assert sdk_client.get_lock(document["id"]).session_id == "session-1"

    with pytest.raises(LockConflictError):
        sdk_client.acquire_lock(document["id"], locked_by="bob", session_id="session-2")

    sdk_client.release_lock(document["id"], released_by="alice")
    assert sdk_client.get_lock(document["id"]) is None


def test_delete_document_and_folder(sdk_client):
    folder = _create_folder()
    document = _upload_document(folder_id=folder["id"])

    sdk_client.delete_document(document["id"], deleted_by="connector-sdk-tests")
    sdk_client.delete_folder(folder["id"])

    with pytest.raises(PathNotFoundError):
        sdk_client.delete_folder(folder["id"])
