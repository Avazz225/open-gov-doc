import base64
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from archival_service import crypto, repository
from archival_service.keystore import EnvKeyStore
from archival_service.main import app
from fastapi.testclient import TestClient

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")


ARCHIVAL_TEST_PRINCIPAL_ID = "archival-service-tests"
# Post-Roadmap Phase 19 Session 6 (ADR 0071): `PUT /roles/{id}` verlangt seit
# dieser Session `admin.user_management` - separates Testprincipal fuer
# `everyone_role_without` unten (siehe `_grant_role_admin_permission`).
ROLE_ADMIN_PRINCIPAL_ID = "archival-service-test-role-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_role_admin_permission():
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-users")
        existing = (
            await pc.get("/role-assignments", params={"principal_id": ROLE_ADMIN_PRINCIPAL_ID})
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": ROLE_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture
def client():
    """Externe Service-Clients (document-/rendering-/storage-/object-type-
    service) durch Fakes ersetzt - identisches Muster wie reporting-
    service's `client`-Fixture: die eigentliche Pipeline-Logik ist bereits
    in test_pipeline.py gegen diese Clients getestet, hier geht es um die
    Endpunkt-Verdrahtung. `permission_client` bleibt UNGEMOCKT (echter Aufruf
    gegen den laufenden permission-service, gleiche "kein Mocking von
    Sibling-Services"-Philosophie wie case-service) - der TestClient traegt
    daher standardmaessig einen `X-DMS-Principal`-Header (RBAC seit
    Post-Roadmap Phase 19 Session 7, ADR 0072; die "everyone"-Gruppe gewaehrt
    `archival.read`/`.write` jedem authentifizierten Principal, kein
    Rollen-Setup fuer den Positivfall noetig). Einzelne Tests koennen den
    Header per `headers={"X-DMS-Principal": ""}` ueberschreiben, um den
    Negativfall zu pruefen."""
    with TestClient(app, headers={"X-DMS-Principal": ARCHIVAL_TEST_PRINCIPAL_ID}) as c:
        app.state.document_client = AsyncMock()
        app.state.rendering_client = AsyncMock()
        app.state.storage_client = AsyncMock()
        app.state.object_type_client = AsyncMock()
        app.state.case_client = AsyncMock()
        app.state.case_client.get_archival_config.return_value = {
            "archive_encryption_enabled": False
        }
        app.state.keystore = EnvKeyStore(None)
        yield c


@pytest.fixture
def everyone_role_without():
    """Entfernt eine Berechtigung temporär aus der geseedeten "everyone"-
    Rolle, um den Negativpfad (fehlende Berechtigung -> 403) zu beweisen -
    gleiches Muster wie case-service/auth-service (dupliziert statt geteilt,
    Projektkonvention). Seit Post-Roadmap Phase 19 Session 6 (ADR 0071)
    verlangt `PUT /roles/{id}` zusätzlich `admin.user_management`."""
    role_management_headers = {"X-DMS-Principal": ROLE_ADMIN_PRINCIPAL_ID}
    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=10.0) as pc:
        roles = pc.get("/roles").json()
        everyone = next(r for r in roles if r["name"] == "everyone")
        original_permissions = list(everyone["permissions"])

        def _remove(permission: str) -> None:
            pc.put(
                f"/roles/{everyone['id']}",
                json={
                    "description": everyone["description"],
                    "permissions": [p for p in original_permissions if p != permission],
                },
                headers=role_management_headers,
            ).raise_for_status()

        yield _remove

        pc.put(
            f"/roles/{everyone['id']}",
            json={"description": everyone["description"], "permissions": original_permissions},
            headers=role_management_headers,
        ).raise_for_status()


def test_list_archival_transfers_without_principal_header_is_401(client):
    response = client.get("/archival-transfers", headers={"X-DMS-Principal": ""})
    assert response.status_code == 401


def test_list_archival_transfers_without_everyone_permission_is_403(client, everyone_role_without):
    everyone_role_without("archival.read")
    response = client.get("/archival-transfers")
    assert response.status_code == 403


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "archival-service"


async def test_list_archival_transfers_empty(client):
    response = client.get("/archival-transfers")
    assert response.status_code == 200
    assert response.json() == []


async def test_get_archival_transfer_returns_404_for_unknown_id(client):
    response = client.get("/archival-transfers/does-not-exist")
    assert response.status_code == 404


async def test_list_and_get_archival_transfer_roundtrip(client, session):
    transfer = await repository.create_transfer(session, "doc-1")
    await session.commit()

    listed = client.get("/archival-transfers").json()
    assert [t["id"] for t in listed] == [transfer.id]

    fetched = client.get(f"/archival-transfers/{transfer.id}")
    assert fetched.status_code == 200
    assert fetched.json()["document_id"] == "doc-1"


async def test_list_archival_transfers_filters_by_status(client, session):
    await repository.create_transfer(session, "doc-pending")
    released = await repository.create_transfer(session, "doc-released")
    await repository.update_status(
        session, released, status="released", released_at=datetime.now(UTC)
    )
    await session.commit()

    response = client.get("/archival-transfers", params={"status": "released"})

    assert [t["document_id"] for t in response.json()] == ["doc-released"]


async def test_retrieve_requires_configured_role(client, session):
    transfer = await repository.create_transfer(session, "doc-1")
    await session.commit()

    response = client.post(f"/archival-transfers/{transfer.id}/retrieve")

    assert response.status_code == 403


async def test_retrieve_returns_404_for_unknown_transfer(client):
    response = client.post(
        "/archival-transfers/does-not-exist/retrieve", headers={"X-DMS-Roles": "dms-admin"}
    )

    assert response.status_code == 404


async def test_retrieve_returns_409_for_pending_transfer(client, session):
    transfer = await repository.create_transfer(session, "doc-1")
    await session.commit()

    response = client.post(
        f"/archival-transfers/{transfer.id}/retrieve", headers={"X-DMS-Roles": "dms-admin"}
    )

    assert response.status_code == 409


async def test_retrieve_writes_back_to_live_target_and_marks_rehydrated(client, session):
    transfer = await repository.create_transfer(session, "doc-1")
    await repository.update_status(
        session,
        transfer,
        status="dehydrated",
        released_at=datetime.now(UTC),
        dehydrated_at=datetime.now(UTC),
        storage_object_key="archive/doc-1/x.pdf",
        checksum_sha256="abc",
        archive_format="pdf_a",
        encrypted=False,
    )
    await session.commit()

    app.state.storage_client.download_archive_copy.return_value = b"%PDF-restored"
    app.state.document_client.get_document.return_value = {
        "id": "doc-1",
        "current_version_number": 1,
    }
    app.state.document_client.get_version.return_value = {
        "storage_object_key": "documents/doc-1/abc",
        "content_type": "application/pdf",
    }

    response = client.post(
        f"/archival-transfers/{transfer.id}/retrieve", headers={"X-DMS-Roles": "dms-admin"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "released"
    assert body["dehydrated_at"] is None
    assert body["rehydrated_at"] is not None
    app.state.storage_client.upload.assert_awaited_once_with(
        "documents/doc-1/abc", b"%PDF-restored", "application/pdf"
    )
    app.state.document_client.mark_rehydrated.assert_awaited_once_with("doc-1")


async def test_list_released_items_requires_configured_role(client, session):
    transfer = await repository.create_transfer(session, "doc-1")
    await repository.update_status(
        session, transfer, status="released", released_at=datetime.now(UTC)
    )
    await session.commit()

    response = client.get("/released-items")

    assert response.status_code == 403


async def test_list_released_items_empty(client):
    response = client.get("/released-items", headers={"X-DMS-Roles": "dms-admin"})

    assert response.status_code == 200
    assert response.json() == []


async def test_list_released_items_excludes_non_released_transfers(client, session):
    await repository.create_transfer(session, "doc-pending")
    dehydrated = await repository.create_transfer(session, "doc-dehydrated")
    await repository.update_status(
        session,
        dehydrated,
        status="dehydrated",
        released_at=datetime.now(UTC),
        dehydrated_at=datetime.now(UTC),
    )
    await session.commit()

    response = client.get("/released-items", headers={"X-DMS-Roles": "dms-admin"})

    assert response.json() == []


async def test_list_released_items_returns_hydrated_document_and_case(client, session):
    doc_transfer = await repository.create_transfer(session, "doc-1")
    await repository.update_status(
        session, doc_transfer, status="released", released_at=datetime.now(UTC)
    )
    case_transfer = await repository.create_case_transfer(session, "case-1")
    await repository.update_status(
        session, case_transfer, status="released", released_at=datetime.now(UTC)
    )
    await session.commit()

    app.state.document_client.get_document.return_value = {
        "title": "Rueckmeldung Buergeranfrage",
        "attributes": {"Kennzeichen": "2026-042"},
    }
    app.state.case_client.get_case.return_value = {
        "name": "Bauantrag Musterstrasse",
        "vorgangsnummer": "2026-007",
    }

    response = client.get("/released-items", headers={"X-DMS-Roles": "dms-admin"})

    assert response.status_code == 200
    body = response.json()
    assert {item["kind"] for item in body} == {"document", "case"}
    doc_item = next(i for i in body if i["kind"] == "document")
    assert doc_item["title"] == "Rueckmeldung Buergeranfrage"
    assert doc_item["identifier"] == "2026-042"
    assert doc_item["purge_at"] is not None
    case_item = next(i for i in body if i["kind"] == "case")
    assert case_item["identifier"] == "2026-007"
    assert case_item["purge_at"] is None


async def test_list_released_items_filters_by_query(client, session):
    doc_transfer = await repository.create_transfer(session, "doc-1")
    await repository.update_status(
        session, doc_transfer, status="released", released_at=datetime.now(UTC)
    )
    await session.commit()

    app.state.document_client.get_document.return_value = {
        "title": "Rueckmeldung Buergeranfrage",
        "attributes": {"Kennzeichen": "2026-042"},
    }

    response = client.get(
        "/released-items", params={"q": "does-not-match"}, headers={"X-DMS-Roles": "dms-admin"}
    )

    assert response.json() == []


async def test_list_case_archival_transfers_empty(client):
    response = client.get("/case-archival-transfers")
    assert response.status_code == 200
    assert response.json() == []


async def test_get_case_archival_transfer_returns_404_for_unknown_id(client):
    response = client.get("/case-archival-transfers/does-not-exist")
    assert response.status_code == 404


async def test_list_and_get_case_archival_transfer_roundtrip(client, session):
    transfer = await repository.create_case_transfer(session, "case-1")
    await session.commit()

    listed = client.get("/case-archival-transfers").json()
    assert [t["id"] for t in listed] == [transfer.id]

    fetched = client.get(f"/case-archival-transfers/{transfer.id}")
    assert fetched.status_code == 200
    assert fetched.json()["case_id"] == "case-1"


async def test_download_case_archival_package_requires_configured_role(client, session):
    transfer = await repository.create_case_transfer(session, "case-1")
    await session.commit()

    response = client.get(f"/case-archival-transfers/{transfer.id}/package")

    assert response.status_code == 403


async def test_download_case_archival_package_returns_404_for_unknown_transfer(client):
    response = client.get(
        "/case-archival-transfers/does-not-exist/package", headers={"X-DMS-Roles": "dms-admin"}
    )

    assert response.status_code == 404


async def test_download_case_archival_package_returns_409_for_pending_transfer(client, session):
    transfer = await repository.create_case_transfer(session, "case-1")
    await session.commit()

    response = client.get(
        f"/case-archival-transfers/{transfer.id}/package", headers={"X-DMS-Roles": "dms-admin"}
    )

    assert response.status_code == 409


async def test_download_case_archival_package_returns_decrypted_zip(client, session):
    key_b64 = base64.b64encode(b"k" * 32).decode("ascii")
    app.state.keystore = EnvKeyStore(key_b64)
    plaintext = b"PK\x03\x04-fake-zip-bytes"
    encrypted = crypto.encrypt(plaintext, app.state.keystore.get_key("default"))

    transfer = await repository.create_case_transfer(session, "case-1")
    await repository.update_status(
        session,
        transfer,
        status="released",
        storage_object_key="archive-case/case-1/x.zip.enc",
        checksum_sha256="abc",
        encrypted=True,
    )
    await session.commit()
    app.state.storage_client.download_archive_copy.return_value = encrypted

    response = client.get(
        f"/case-archival-transfers/{transfer.id}/package", headers={"X-DMS-Roles": "dms-admin"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.content == plaintext
