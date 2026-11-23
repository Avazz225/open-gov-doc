"""Listings show only what the caller may read (P91-S4, ADR 0259).

Isolates a folder from the default "everyone" grant (`inherit=False`, the
mechanism teamspace roots use) and checks the child listing, the service
hidden-count header and the breadcrumb placeholder."""

import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from folder_service.main import app
from folder_service.settings import Settings

settings = Settings()
PERMISSION_SERVICE_URL = settings.permission_service_base_url
ROLE_ADMIN_HEADERS = {"X-DMS-Principal": "folder-service-test-role-admin"}


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "folder-service-tests"}) as c:
        yield c


def _folder(client, name: str, parent_id: str = "root") -> dict:
    response = client.post("/folders", json={"name": name, "parent_id": parent_id})
    assert response.status_code == 201, response.text
    folder = response.json()
    # Folder resource nodes arrive via `folder.resource.created`.
    for _ in range(50):
        if httpx.get(f"{PERMISSION_SERVICE_URL}/resources/{folder['id']}").status_code == 200:
            return folder
        time.sleep(0.1)
    raise AssertionError(f"resource node for {folder['id']} never appeared")


def _isolate(folder_id: str) -> None:
    httpx.patch(
        f"{PERMISSION_SERVICE_URL}/resources/{folder_id}", json={"inherit": False}
    ).raise_for_status()


def _grant_read(principal: str, folder_id: str) -> None:
    role = httpx.post(
        f"{PERMISSION_SERVICE_URL}/roles",
        json={"name": f"folder-reader-{uuid.uuid4().hex[:8]}", "permissions": ["folder.read"]},
        headers=ROLE_ADMIN_HEADERS,
    ).json()
    httpx.post(
        f"{PERMISSION_SERVICE_URL}/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": principal,
            "role_id": role["role"]["id"],
            "resource_id": folder_id,
        },
    ).raise_for_status()


def test_children_the_caller_cannot_read_are_left_out(client):
    parent = _folder(client, "Abteilung")
    open_child = _folder(client, "Offen", parent["id"])
    closed_child = _folder(client, "Teamspace geschlossen", parent["id"])
    _isolate(closed_child["id"])

    outsider = {"X-DMS-Principal": f"outsider-{uuid.uuid4().hex[:6]}"}
    listing = client.get(f"/folders/{parent['id']}/children", headers=outsider)
    assert listing.status_code == 200
    assert [f["id"] for f in listing.json()] == [open_child["id"]]
    # Users don't learn that something was hidden.
    assert "x-dms-hidden-count" not in listing.headers

    # A service principal does, so a migration can refuse to skip silently.
    as_service = client.get(
        f"/folders/{parent['id']}/children", headers={"X-DMS-Principal": "migration-service"}
    )
    assert as_service.headers["x-dms-hidden-count"] == "1"


def test_breadcrumb_shows_an_unreadable_ancestor_as_a_placeholder(client):
    closed = _folder(client, "Geheimprojekt")
    inner = _folder(client, "Freigegeben", closed["id"])
    _isolate(closed["id"])
    _isolate(inner["id"])
    reader = f"reader-{uuid.uuid4().hex[:6]}"
    _grant_read(reader, inner["id"])

    response = client.get(f"/folders/{inner['id']}/breadcrumb", headers={"X-DMS-Principal": reader})
    assert response.status_code == 200, response.text
    chain = response.json()
    assert [f["id"] for f in chain] == ["root", closed["id"], inner["id"]]
    placeholder = chain[1]
    assert placeholder["restricted"] is True
    assert placeholder["name"] == ""
    assert placeholder["created_by"] == ""
    assert chain[2]["name"] == "Freigegeben"
    assert chain[2]["restricted"] is False
