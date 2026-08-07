import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from archival_service import crypto, repository
from archival_service.keystore import EnvKeyStore
from archival_service.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Externe Service-Clients (document-/rendering-/storage-/object-type-
    service) durch Fakes ersetzt - identisches Muster wie reporting-
    service's `client`-Fixture: die eigentliche Pipeline-Logik ist bereits
    in test_pipeline.py gegen diese Clients getestet, hier geht es um die
    Endpunkt-Verdrahtung."""
    with TestClient(app) as c:
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
