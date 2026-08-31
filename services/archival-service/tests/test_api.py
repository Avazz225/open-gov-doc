import base64
import io
import os
import zipfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from archival_service import crypto, repository, xdomea, xjustiz
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


async def test_retry_returns_404_for_unknown_transfer(client):
    response = client.post("/archival-transfers/does-not-exist/retry")

    assert response.status_code == 404


async def test_retry_returns_409_for_still_active_transfer(client, session):
    transfer = await repository.create_transfer(session, "doc-1")
    await session.commit()

    response = client.post(f"/archival-transfers/{transfer.id}/retry")

    assert response.status_code == 409


async def test_retry_resets_a_failed_permanent_transfer_to_pending(client, session):
    transfer = await repository.create_transfer(session, "doc-1")
    await repository.mark_failed(session, transfer, error_message="boom", max_attempts=1)
    await session.commit()

    response = client.post(f"/archival-transfers/{transfer.id}/retry")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["attempts"] == 0
    assert body["next_retry_at"] is None
    assert body["error_message"] is None


async def test_retry_without_write_permission_is_403(client, session, everyone_role_without):
    transfer = await repository.create_transfer(session, "doc-1")
    await repository.mark_failed(session, transfer, error_message="boom", max_attempts=1)
    await session.commit()
    everyone_role_without("archival.write")

    response = client.post(f"/archival-transfers/{transfer.id}/retry")

    assert response.status_code == 403


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


async def test_retry_case_transfer_returns_409_for_still_active_transfer(client, session):
    transfer = await repository.create_case_transfer(session, "case-1")
    await session.commit()

    response = client.post(f"/case-archival-transfers/{transfer.id}/retry")

    assert response.status_code == 409


async def test_retry_case_transfer_resets_a_failed_permanent_transfer_to_pending(client, session):
    transfer = await repository.create_case_transfer(session, "case-1")
    await repository.mark_failed(session, transfer, error_message="boom", max_attempts=1)
    await session.commit()

    response = client.post(f"/case-archival-transfers/{transfer.id}/retry")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["attempts"] == 0


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


# --- General XDOMEA export (Abgabe.Abgabe.0401, 14.2, Post-Roadmap Phase 31
# Session 13a, ADR 0126) --------------------------------------------------


def test_export_document_xdomea_without_principal_header_is_401(client):
    response = client.post(
        "/xdomea/export/documents/doc-1",
        params={"leser_name": "Andere Behoerde"},
        headers={"X-DMS-Principal": ""},
    )
    assert response.status_code == 401


def test_export_document_xdomea_without_everyone_permission_is_403(client, everyone_role_without):
    everyone_role_without("archival.write")
    response = client.post(
        "/xdomea/export/documents/doc-1", params={"leser_name": "Andere Behoerde"}
    )
    assert response.status_code == 403


def test_export_document_xdomea_requires_non_empty_leser_name(client):
    response = client.post("/xdomea/export/documents/doc-1", params={"leser_name": "  "})
    assert response.status_code == 422


async def test_export_document_xdomea_returns_404_for_unknown_document(client):
    app.state.document_client.get_document.side_effect = httpx.HTTPStatusError(
        "not found", request=httpx.Request("GET", "http://x"), response=httpx.Response(404)
    )

    response = client.post(
        "/xdomea/export/documents/does-not-exist", params={"leser_name": "Andere Behoerde"}
    )

    assert response.status_code == 404


async def test_export_document_xdomea_returns_a_valid_zip_package(client):
    app.state.document_client.get_document.return_value = {
        "id": "doc-1",
        "current_version_number": 1,
    }
    app.state.document_client.get_version.return_value = {
        "content_type": "application/pdf",
        "filename": "schreiben.pdf",
    }
    app.state.document_client.download_version_content.return_value = b"%PDF-fake-content"

    response = client.post(
        "/xdomea/export/documents/doc-1", params={"leser_name": "Andere Behoerde"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "abgabe.xml" in names
        assert any(n.startswith("dokumente/") for n in names)
        message_xml = archive.read("abgabe.xml")
    xdomea.validate_abgabe_message(message_xml)


async def test_export_case_xdomea_returns_404_for_unknown_case(client):
    app.state.case_client.get_case.side_effect = httpx.HTTPStatusError(
        "not found", request=httpx.Request("GET", "http://x"), response=httpx.Response(404)
    )

    response = client.post(
        "/xdomea/export/cases/does-not-exist", params={"leser_name": "Andere Behoerde"}
    )

    assert response.status_code == 404


async def test_export_case_xdomea_returns_409_when_a_referenced_document_is_gone(client):
    """Found live during this session's own verification against the real
    dev stack: a case's `CaseDocumentReference` pointed at a document that
    had since been deleted from document-service - a blanket 404 catch
    would have mislabeled this as "case unknown" (misleading, since the
    case itself is real). Must surface as 409 (data drift), not 404."""
    app.state.case_client.get_case.return_value = {"id": "case-1", "name": "Testfall Abgabe"}
    app.state.case_client.list_document_references.return_value = [
        {"document_id": "doc-gone", "snapshot_version_number": 1, "removed_at": None},
    ]
    app.state.document_client.get_version.side_effect = httpx.HTTPStatusError(
        "not found", request=httpx.Request("GET", "http://x"), response=httpx.Response(404)
    )

    response = client.post("/xdomea/export/cases/case-1", params={"leser_name": "Andere Behoerde"})

    assert response.status_code == 409
    assert "doc-gone" in response.json()["detail"]


async def test_export_case_xdomea_returns_a_valid_zip_package_excluding_removed_references(client):
    app.state.case_client.get_case.return_value = {"id": "case-1", "name": "Testfall Abgabe"}
    app.state.case_client.list_document_references.return_value = [
        {"document_id": "doc-1", "snapshot_version_number": 1, "removed_at": None},
        {
            "document_id": "doc-2",
            "snapshot_version_number": 1,
            "removed_at": "2026-01-01T00:00:00Z",
        },
    ]
    app.state.document_client.get_version.return_value = {
        "content_type": "application/pdf",
        "filename": "schreiben.pdf",
    }
    app.state.document_client.download_version_content.return_value = b"%PDF-fake-content"

    response = client.post("/xdomea/export/cases/case-1", params={"leser_name": "Andere Behoerde"})

    assert response.status_code == 200
    app.state.document_client.get_version.assert_called_once_with("doc-1", 1)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        message_xml = archive.read("abgabe.xml")
    xdomea.validate_abgabe_message(message_xml)


# --- General XDOMEA import (14.2, Post-Roadmap Phase 31 Session 13b, ADR 0128) --


def _build_case_package(*, name="Testfall Import", documents=None) -> bytes:
    documents = (
        documents
        if documents is not None
        else [
            {
                "document_id": "src-doc-1",
                "version_number": 1,
                "content_type": "application/pdf",
                "original_filename": "schreiben.pdf",
                "package_filename": xdomea.package_filename("src-doc-1", 1, "application/pdf"),
            }
        ]
    )
    case = {"id": "src-case-1", "name": name}
    message_xml = xdomea.build_abgabe_message_for_case(case, documents, leser_name="DMS")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("abgabe.xml", message_xml)
        for doc in documents:
            archive.writestr(f"dokumente/{doc['package_filename']}", b"%PDF-fake-content")
    return buffer.getvalue()


def _build_document_package() -> bytes:
    document = {
        "document_id": "src-doc-2",
        "version_number": 1,
        "content_type": "application/pdf",
        "original_filename": "einzeldok.pdf",
        "package_filename": xdomea.package_filename("src-doc-2", 1, "application/pdf"),
    }
    message_xml = xdomea.build_abgabe_message_for_document(document, leser_name="DMS")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("abgabe.xml", message_xml)
        archive.writestr(f"dokumente/{document['package_filename']}", b"%PDF-fake-content")
    return buffer.getvalue()


def test_import_xdomea_without_principal_header_is_401(client):
    response = client.post(
        "/xdomea/import",
        data={"folder_id": "root"},
        files={"file": ("abgabe.zip", _build_document_package(), "application/zip")},
        headers={"X-DMS-Principal": ""},
    )
    assert response.status_code == 401


def test_import_xdomea_without_everyone_permission_is_403(client, everyone_role_without):
    everyone_role_without("archival.write")
    response = client.post(
        "/xdomea/import",
        data={"folder_id": "root"},
        files={"file": ("abgabe.zip", _build_document_package(), "application/zip")},
    )
    assert response.status_code == 403


def test_import_xdomea_rejects_a_non_zip_file(client):
    response = client.post(
        "/xdomea/import",
        data={"folder_id": "root"},
        files={"file": ("abgabe.zip", b"not a zip file", "application/zip")},
    )
    assert response.status_code == 422


def test_import_xdomea_rejects_case_id_and_process_definition_id_together(client):
    response = client.post(
        "/xdomea/import",
        data={"folder_id": "root", "case_id": "case-1", "process_definition_id": "1"},
        files={"file": ("abgabe.zip", _build_case_package(), "application/zip")},
    )
    assert response.status_code == 422


def test_import_xdomea_requires_a_case_target_when_package_has_a_vorgang(client):
    response = client.post(
        "/xdomea/import",
        data={"folder_id": "root"},
        files={"file": ("abgabe.zip", _build_case_package(), "application/zip")},
    )
    assert response.status_code == 422


def test_import_xdomea_rejects_process_definition_id_without_a_vorgang(client):
    response = client.post(
        "/xdomea/import",
        data={"folder_id": "root", "process_definition_id": "1"},
        files={"file": ("abgabe.zip", _build_document_package(), "application/zip")},
    )
    assert response.status_code == 422


async def test_import_xdomea_standalone_document_creates_it_in_the_target_folder(client):
    app.state.document_client.create_document.return_value = {"id": "new-doc-1"}

    response = client.post(
        "/xdomea/import",
        data={"folder_id": "target-folder"},
        files={"file": ("abgabe.zip", _build_document_package(), "application/zip")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "case_id": None,
        "case_created": False,
        "vorgang_betreff": None,
        "document_ids": ["new-doc-1"],
    }
    app.state.document_client.create_document.assert_called_once()
    call_kwargs = app.state.document_client.create_document.call_args.kwargs
    assert call_kwargs["folder_id"] == "target-folder"
    assert call_kwargs["data"] == b"%PDF-fake-content"
    app.state.case_client.add_document_reference.assert_not_called()


async def test_import_xdomea_case_package_attaches_to_an_existing_case(client):
    app.state.document_client.create_document.return_value = {"id": "new-doc-2"}

    response = client.post(
        "/xdomea/import",
        data={"folder_id": "target-folder", "case_id": "existing-case-1"},
        files={"file": ("abgabe.zip", _build_case_package(), "application/zip")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "existing-case-1"
    assert body["case_created"] is False
    assert body["vorgang_betreff"] == "Testfall Import"
    assert body["document_ids"] == ["new-doc-2"]
    app.state.case_client.create_case.assert_not_called()
    app.state.case_client.add_document_reference.assert_called_once_with(
        "existing-case-1", document_id="new-doc-2", added_by="archival-service-tests"
    )


async def test_import_xdomea_case_package_creates_a_new_case(client):
    app.state.document_client.create_document.return_value = {"id": "new-doc-3"}
    app.state.case_client.create_case.return_value = {"id": "brand-new-case-1"}

    response = client.post(
        "/xdomea/import",
        data={"folder_id": "target-folder", "process_definition_id": "42"},
        files={"file": ("abgabe.zip", _build_case_package(name="Neuer Fall"), "application/zip")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "brand-new-case-1"
    assert body["case_created"] is True
    assert body["vorgang_betreff"] == "Neuer Fall"
    app.state.case_client.create_case.assert_called_once()
    create_kwargs = app.state.case_client.create_case.call_args.kwargs
    assert create_kwargs["name"] == "Neuer Fall"
    assert create_kwargs["process_definition_id"] == 42
    app.state.case_client.add_document_reference.assert_called_once_with(
        "brand-new-case-1", document_id="new-doc-3", added_by="archival-service-tests"
    )


async def test_import_xdomea_rejects_a_package_missing_a_referenced_content_file(client):
    documents = [
        {
            "document_id": "src-doc-3",
            "version_number": 1,
            "content_type": "application/pdf",
            "original_filename": "fehlend.pdf",
            "package_filename": xdomea.package_filename("src-doc-3", 1, "application/pdf"),
        }
    ]
    case = {"id": "src-case-2", "name": "Fall ohne Dateien"}
    message_xml = xdomea.build_abgabe_message_for_case(case, documents, leser_name="DMS")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("abgabe.xml", message_xml)
        # Deliberately NOT writing the "dokumente/..." entry the message references.
    incomplete_package = buffer.getvalue()

    response = client.post(
        "/xdomea/import",
        data={"folder_id": "root", "case_id": "case-1"},
        files={"file": ("abgabe.zip", incomplete_package, "application/zip")},
    )

    assert response.status_code == 422


# --- General XJustiz export (14.2, Post-Roadmap Phase 31 Session 13c, ADR 0129) --


def test_export_document_xjustiz_without_principal_header_is_401(client):
    response = client.post(
        "/xjustiz/export/documents/doc-1",
        params={"empfaenger_name": "Testgericht"},
        headers={"X-DMS-Principal": ""},
    )
    assert response.status_code == 401


def test_export_document_xjustiz_without_everyone_permission_is_403(client, everyone_role_without):
    everyone_role_without("archival.write")
    response = client.post(
        "/xjustiz/export/documents/doc-1", params={"empfaenger_name": "Testgericht"}
    )
    assert response.status_code == 403


def test_export_document_xjustiz_requires_non_empty_empfaenger_name(client):
    response = client.post("/xjustiz/export/documents/doc-1", params={"empfaenger_name": "  "})
    assert response.status_code == 422


async def test_export_document_xjustiz_returns_404_for_unknown_document(client):
    app.state.document_client.get_document.side_effect = httpx.HTTPStatusError(
        "not found", request=httpx.Request("GET", "http://x"), response=httpx.Response(404)
    )

    response = client.post(
        "/xjustiz/export/documents/does-not-exist", params={"empfaenger_name": "Testgericht"}
    )

    assert response.status_code == 404


async def test_export_document_xjustiz_returns_a_valid_zip_package(client):
    app.state.document_client.get_document.return_value = {
        "id": "doc-1",
        "title": "Testschreiben",
        "current_version_number": 1,
    }
    app.state.document_client.get_version.return_value = {
        "content_type": "application/pdf",
        "filename": "schreiben.pdf",
    }
    app.state.document_client.download_version_content.return_value = b"%PDF-fake-content"

    response = client.post(
        "/xjustiz/export/documents/doc-1", params={"empfaenger_name": "Testgericht"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "xjustiz_nachricht.xml" in names
        assert any(n.startswith("dokumente/") for n in names)
        message_xml = archive.read("xjustiz_nachricht.xml")
    xjustiz.validate_uebermittlung_schriftgutobjekte(message_xml)


async def test_export_case_xjustiz_returns_404_for_unknown_case(client):
    app.state.case_client.get_case.side_effect = httpx.HTTPStatusError(
        "not found", request=httpx.Request("GET", "http://x"), response=httpx.Response(404)
    )

    response = client.post(
        "/xjustiz/export/cases/does-not-exist", params={"empfaenger_name": "Testgericht"}
    )

    assert response.status_code == 404


async def test_export_case_xjustiz_returns_409_when_a_referenced_document_is_gone(client):
    app.state.case_client.get_case.return_value = {"id": "case-1", "name": "Testfall"}
    app.state.case_client.list_document_references.return_value = [
        {"document_id": "doc-gone", "snapshot_version_number": 1, "removed_at": None},
    ]
    app.state.document_client.get_version.side_effect = httpx.HTTPStatusError(
        "not found", request=httpx.Request("GET", "http://x"), response=httpx.Response(404)
    )

    response = client.post(
        "/xjustiz/export/cases/case-1", params={"empfaenger_name": "Testgericht"}
    )

    assert response.status_code == 409
    assert "doc-gone" in response.json()["detail"]


async def test_export_case_xjustiz_returns_a_valid_zip_package_excluding_removed_references(client):
    app.state.case_client.get_case.return_value = {"id": "case-1", "name": "Testfall XJustiz"}
    app.state.case_client.list_document_references.return_value = [
        {"document_id": "doc-1", "snapshot_version_number": 1, "removed_at": None},
        {
            "document_id": "doc-2",
            "snapshot_version_number": 1,
            "removed_at": "2026-01-01T00:00:00Z",
        },
    ]
    app.state.document_client.get_version.return_value = {
        "content_type": "application/pdf",
        "filename": "schreiben.pdf",
    }
    app.state.document_client.download_version_content.return_value = b"%PDF-fake-content"

    response = client.post(
        "/xjustiz/export/cases/case-1", params={"empfaenger_name": "Testgericht"}
    )

    assert response.status_code == 200
    app.state.document_client.get_version.assert_called_once_with("doc-1", 1)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        message_xml = archive.read("xjustiz_nachricht.xml")
    xjustiz.validate_uebermittlung_schriftgutobjekte(message_xml)
