import pytest
from fastapi.testclient import TestClient
from favorite_service.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _headers(user_id: str) -> dict[str, str]:
    return {"X-DMS-Username": user_id}


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "favorite-service"


def test_create_and_list(client):
    create_response = client.post(
        "/favorites",
        json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"},
        headers=_headers("alice"),
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["user_id"] == "alice"
    assert body["object_type"] == "document"
    assert body["object_id"] == "doc-1"

    list_response = client.get("/favorites", params={"user_id": "alice"}, headers=_headers("alice"))
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_duplicate_returns_409(client):
    payload = {"user_id": "alice", "object_type": "folder", "object_id": "folder-1"}
    client.post("/favorites", json=payload, headers=_headers("alice"))
    response = client.post("/favorites", json=payload, headers=_headers("alice"))
    assert response.status_code == 409


def test_list_filters_by_object_type(client):
    client.post(
        "/favorites",
        json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"},
        headers=_headers("alice"),
    )
    client.post(
        "/favorites",
        json={"user_id": "alice", "object_type": "folder", "object_id": "folder-1"},
        headers=_headers("alice"),
    )
    client.post(
        "/favorites",
        json={"user_id": "alice", "object_type": "case", "object_id": "case-1"},
        headers=_headers("alice"),
    )

    response = client.get(
        "/favorites",
        params={"user_id": "alice", "object_type": "folder"},
        headers=_headers("alice"),
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["object_type"] == "folder"


def test_create_list_and_delete_a_case_favorite(client):
    """Phase 45 Session 2 - `"case"` added as a third `object_type`
    alongside `document`/`folder`, deliberately deferred at plan approval
    (P7-S1d) until `case-service` had a browsing UI to add a favorite
    toggle to ("Umlaufmappen", ADR 0141, Phase 34). No repository/model
    change was needed - the DB column is already a generic `String(16)`
    and this service never validates `object_id` against any sibling
    service regardless of type - only the `ObjectType` Literal in
    `schemas.py` needed extending."""
    create_response = client.post(
        "/favorites",
        json={"user_id": "alice", "object_type": "case", "object_id": "case-1"},
        headers=_headers("alice"),
    )
    assert create_response.status_code == 201
    assert create_response.json()["object_type"] == "case"

    list_response = client.get(
        "/favorites",
        params={"user_id": "alice", "object_type": "case"},
        headers=_headers("alice"),
    )
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    delete_response = client.request(
        "DELETE",
        "/favorites",
        params={"user_id": "alice", "object_type": "case", "object_id": "case-1"},
        headers=_headers("alice"),
    )
    assert delete_response.status_code == 204


def test_list_scoped_to_user(client):
    client.post(
        "/favorites",
        json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"},
        headers=_headers("alice"),
    )
    client.post(
        "/favorites",
        json={"user_id": "bob", "object_type": "document", "object_id": "doc-2"},
        headers=_headers("bob"),
    )

    response = client.get("/favorites", params={"user_id": "bob"}, headers=_headers("bob"))
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["object_id"] == "doc-2"


def test_delete_removes_favorite(client):
    client.post(
        "/favorites",
        json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"},
        headers=_headers("alice"),
    )

    delete_response = client.request(
        "DELETE",
        "/favorites",
        params={"user_id": "alice", "object_type": "document", "object_id": "doc-1"},
        headers=_headers("alice"),
    )
    assert delete_response.status_code == 204

    list_response = client.get("/favorites", params={"user_id": "alice"}, headers=_headers("alice"))
    assert list_response.json() == []


def test_delete_unknown_returns_404(client):
    response = client.request(
        "DELETE",
        "/favorites",
        params={"user_id": "alice", "object_type": "document", "object_id": "unknown"},
        headers=_headers("alice"),
    )
    assert response.status_code == 404


# --- IDOR fix regression tests (P54-S3) -------------------------------------


def test_create_favorite_for_another_user_is_rejected(client):
    response = client.post(
        "/favorites",
        json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"},
        headers=_headers("bob"),
    )
    assert response.status_code == 403


def test_create_favorite_without_principal_header_returns_401(client):
    response = client.post(
        "/favorites", json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"}
    )
    assert response.status_code == 401


def test_list_favorites_for_another_user_is_rejected(client):
    client.post(
        "/favorites",
        json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"},
        headers=_headers("alice"),
    )

    response = client.get("/favorites", params={"user_id": "alice"}, headers=_headers("bob"))
    assert response.status_code == 403


def test_delete_favorite_for_another_user_is_rejected(client):
    client.post(
        "/favorites",
        json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"},
        headers=_headers("alice"),
    )

    response = client.request(
        "DELETE",
        "/favorites",
        params={"user_id": "alice", "object_type": "document", "object_id": "doc-1"},
        headers=_headers("bob"),
    )
    assert response.status_code == 403

    # The favorite must genuinely survive the rejected attempt, not just the
    # response code - confirms this is a real authorization check, not a
    # cosmetic one.
    list_response = client.get("/favorites", params={"user_id": "alice"}, headers=_headers("alice"))
    assert len(list_response.json()) == 1
