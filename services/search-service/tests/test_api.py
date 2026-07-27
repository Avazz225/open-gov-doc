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


def _grant_root_read(principal_id: str) -> None:
    role = httpx.post(
        f"{PERMISSION_SERVICE_URL}/roles",
        json={"name": f"search-test-role-{uuid.uuid4().hex[:8]}", "permissions": ["document.read"]},
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


async def _index_at_root(title: str) -> str:
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
        )
        await session.commit()
    await engine.dispose()
    return document_id


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
