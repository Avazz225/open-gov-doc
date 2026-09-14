import os
import uuid
from datetime import UTC, datetime

import httpx
from dms_db_base import build_engine, make_session_factory
from fastapi.testclient import TestClient
from search_service import repository
from search_service.main import app

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Muss mit conftest.py::ROLE_ADMIN_PRINCIPAL_ID übereinstimmen (dort per
# `_grant_role_admin_permission`-Fixture berechtigt) - kein Cross-File-Import
# von Test-Konstanten, gleiche Projektkonvention wie andernorts.
ROLE_ADMIN_PRINCIPAL_ID = "search-service-test-role-admin"


def _grant_root_read(principal_id: str) -> None:
    role = httpx.post(
        f"{PERMISSION_SERVICE_URL}/roles",
        json={"name": f"search-test-role-{uuid.uuid4().hex[:8]}", "permissions": ["document.read"]},
        headers={"X-DMS-Principal": ROLE_ADMIN_PRINCIPAL_ID},
        timeout=30.0,
    )
    role.raise_for_status()
    assignment = httpx.post(
        f"{PERMISSION_SERVICE_URL}/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": principal_id,
            # `POST /roles` also wraps its response since P32-S1 (ADR
            # 0130, `RoleActionResult`) - unwrap `["role"]`.
            "role_id": role.json()["role"]["id"],
            "resource_id": "root",
        },
        timeout=30.0,
    )
    assignment.raise_for_status()


async def _index_at_root(title: str, *, registered_at: datetime | None = None) -> str:
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)
    now = datetime.now(UTC)
    async with session_factory() as session:
        await repository.upsert_document(
            session,
            document_id=document_id,
            title=title,
            folder_id=None,
            folder_name=None,
            object_type_id=None,
            attributes={},
            current_version_number=1,
            full_text="",
            created_by="search-service-tests",
            created_at=now,
            updated_at=now,
            registered_at=registered_at,
        )
        await session.commit()
    await engine.dispose()
    return document_id


FOLDER_SERVICE_URL = os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")


def _create_folder(name: str) -> str:
    response = httpx.post(
        f"{FOLDER_SERVICE_URL}/folders",
        json={"name": name, "parent_id": "root", "created_by": "search-service-tests"},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["id"]


def _grant_folder_read(principal_id: str, folder_id: str) -> None:
    role = httpx.post(
        f"{PERMISSION_SERVICE_URL}/roles",
        json={
            "name": f"search-test-hf-role-{uuid.uuid4().hex[:8]}",
            "permissions": ["folder.read"],
        },
        headers={"X-DMS-Principal": ROLE_ADMIN_PRINCIPAL_ID},
        timeout=30.0,
    )
    role.raise_for_status()
    assignment = httpx.post(
        f"{PERMISSION_SERVICE_URL}/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": principal_id,
            "role_id": role.json()["role"]["id"],
            "resource_id": folder_id,
        },
        timeout=30.0,
    )
    assignment.raise_for_status()


async def _index_folder_reference(folder_id: str, *, folder_name: str, document_id: str) -> None:
    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        await repository.upsert_folder_reference(
            session,
            folder_id=folder_id,
            document_id=document_id,
            folder_name=folder_name,
            added_by="search-service-tests",
            added_at=datetime.now(UTC),
        )
        await session.commit()
    await engine.dispose()


def test_healthz():
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "search-service"


def test_search_requires_principal_header():
    with TestClient(app) as client:
        response = client.get("/search", params={"q": "irrelevant"})
    assert response.status_code == 401


def test_search_facets_returns_document_object_types():
    with TestClient(app) as client:
        response = client.get("/search/facets")
    assert response.status_code == 200
    assert "object_types" in response.json()


def test_search_with_malformed_query_returns_400():
    with TestClient(app) as client:
        response = client.get(
            "/search",
            params={"q": "a (b or c"},
            headers={"X-DMS-Principal": "irrelevant"},
        )
    assert response.status_code == 400


async def test_search_finds_fuzzy_match_over_http():
    unique = uuid.uuid4().hex[:8]
    document_id = await _index_at_root(f"Sonderbegriff {unique}")
    principal_id = f"alice-{uuid.uuid4().hex[:8]}"
    _grant_root_read(principal_id)

    with TestClient(app) as client:
        response = client.get(
            "/search",
            # Absichtlicher Tippfehler (fehlendes 'f')
            params={"q": f"Sonderbegrif~ {unique}"},
            headers={"X-DMS-Principal": principal_id},
        )
    assert response.status_code == 200
    assert any(r["id"] == document_id for r in response.json()["results"])


async def test_search_only_returns_documents_the_principal_may_read():
    title = f"Sondertitel-{uuid.uuid4().hex[:8]}"
    await _index_at_root(title)

    allowed_principal = f"alice-{uuid.uuid4().hex[:8]}"
    denied_principal = f"bob-{uuid.uuid4().hex[:8]}"
    _grant_root_read(allowed_principal)

    with TestClient(app) as client:
        allowed_response = client.get(
            "/search", params={"q": title}, headers={"X-DMS-Principal": allowed_principal}
        )
        denied_response = client.get(
            "/search", params={"q": title}, headers={"X-DMS-Principal": denied_principal}
        )

    assert allowed_response.status_code == 200
    assert any(r["title"] == title for r in allowed_response.json()["results"])

    assert denied_response.status_code == 200
    assert all(r["title"] != title for r in denied_response.json()["results"])


async def test_search_registered_false_lists_only_unregistered_documents_over_http():
    # Work-tray browsing (ADR 0113/0118/0146).
    unique = uuid.uuid4().hex[:8]
    draft_title = f"Entwurf-{unique}"
    registered_title = f"Registriert-{unique}"
    draft_id = await _index_at_root(draft_title, registered_at=None)
    await _index_at_root(registered_title, registered_at=datetime.now(UTC))
    principal_id = f"alice-{unique}"
    _grant_root_read(principal_id)

    with TestClient(app) as client:
        response = client.get(
            "/search",
            params={"registered": "false", "sort": "updated_at"},
            headers={"X-DMS-Principal": principal_id},
        )
    assert response.status_code == 200
    results = response.json()["results"]
    assert any(r["id"] == draft_id for r in results)
    assert all(r["title"] != registered_title for r in results)


def test_folder_references_requires_principal_header():
    with TestClient(app) as client:
        response = client.get("/folder-references")
    assert response.status_code == 401


async def test_folder_references_only_returns_folders_the_principal_may_read():
    unique = uuid.uuid4().hex[:8]
    readable_folder = _create_folder(f"Handakte-lesbar-{unique}")
    hidden_folder = _create_folder(f"Handakte-verborgen-{unique}")
    await _index_folder_reference(
        readable_folder, folder_name="Lesbar", document_id=f"doc-{unique}-a"
    )
    await _index_folder_reference(
        hidden_folder, folder_name="Verborgen", document_id=f"doc-{unique}-b"
    )

    principal_id = f"alice-hf-{unique}"
    _grant_folder_read(principal_id, readable_folder)

    with TestClient(app) as client:
        response = client.get("/folder-references", headers={"X-DMS-Principal": principal_id})
    assert response.status_code == 200
    results = response.json()["results"]
    folder_ids = {r["folder_id"] for r in results}
    assert readable_folder in folder_ids
    assert hidden_folder not in folder_ids
