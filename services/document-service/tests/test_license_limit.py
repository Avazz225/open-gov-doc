import httpx
import pytest
from document_service.license_client import LicenseLimitClient
from document_service.main import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _default_no_license_limit_exceeded():
    """Überschreibt (gleicher Name, engerer Scope) den globalen Autouse-Patch
    aus conftest.py - diese Tests prüfen genau das reale Verhalten von
    `LicenseLimitClient.is_exceeded`."""
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def upload(client, *, content=b"Hallo Welt", title="Vertrag", created_by="alice"):
    data = {"title": title, "created_by": created_by}
    files = {"file": ("vertrag.pdf", content, "application/pdf")}
    return client.post("/documents", data=data, files=files)


def _transport(limits_exceeded: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"installed": True, "valid": True, "limits_exceeded": limits_exceeded}
        )

    return httpx.MockTransport(handler)


async def test_is_exceeded_true_when_dimension_listed():
    client = LicenseLimitClient(
        "http://license.local", cache_ttl_seconds=60.0, transport=_transport(["documents"])
    )
    assert await client.is_exceeded("documents") is True
    await client.close()


async def test_is_exceeded_false_when_dimension_not_listed():
    client = LicenseLimitClient(
        "http://license.local", cache_ttl_seconds=60.0, transport=_transport(["storage_gb"])
    )
    assert await client.is_exceeded("documents") is False
    await client.close()


async def test_is_exceeded_fails_open_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = LicenseLimitClient(
        "http://license.local", cache_ttl_seconds=60.0, transport=httpx.MockTransport(handler)
    )
    assert await client.is_exceeded("documents") is False
    await client.close()


def test_create_document_blocked_when_documents_limit_exceeded(client, monkeypatch):
    async def _exceeded(self, dimension: str) -> bool:
        return dimension == "documents"

    monkeypatch.setattr(LicenseLimitClient, "is_exceeded", _exceeded)

    response = upload(client)

    assert response.status_code == 403


def test_create_document_allowed_when_not_exceeded(client, monkeypatch):
    async def _not_exceeded(self, dimension: str) -> bool:
        return False

    monkeypatch.setattr(LicenseLimitClient, "is_exceeded", _not_exceeded)

    response = upload(client)

    assert response.status_code == 201
