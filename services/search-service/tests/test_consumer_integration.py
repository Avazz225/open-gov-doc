import asyncio
import os
import uuid

import httpx
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event
from fastapi.testclient import TestClient
from search_service import repository
from search_service.consumer import make_text_update_handler
from search_service.document_client import DocumentServiceClient
from search_service.folder_client import FolderServiceClient
from search_service.main import app
from search_service.ocr_client import OcrServiceClient
from search_service.rendering_client import RenderingServiceClient

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
FOLDER_SERVICE_URL = os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
# Same session-scoped `domain-admin-users` principal conftest.py already
# grants for this service's own tests - reused here to grant `folder.write`
# on a throwaway folder (hand-folder references, ADR 0118/0146).
ROLE_ADMIN_PRINCIPAL_ID = "search-service-test-role-admin"


def _upload_document(*, filename: str) -> str:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": filename, "created_by": "search-service-tests"},
        files={"file": (filename, b"Inhalt", "text/plain")},
        timeout=30.0,
        headers={"X-DMS-Principal": "search-service-tests"},
    )
    response.raise_for_status()
    return response.json()["id"]


def _create_folder(name: str) -> str:
    response = httpx.post(
        f"{FOLDER_SERVICE_URL}/folders",
        json={"name": name, "parent_id": "root", "created_by": "search-service-tests"},
        timeout=30.0,
        headers={"X-DMS-Principal": "search-service-tests"},
    )
    response.raise_for_status()
    return response.json()["id"]


def _grant_folder_write(principal_id: str, folder_id: str) -> None:
    role = httpx.post(
        f"{PERMISSION_SERVICE_URL}/roles",
        json={
            "name": f"search-test-hand-folder-role-{uuid.uuid4().hex[:8]}",
            # Hand folders (ADR 0118) gate `.../document-references` via
            # their OWN dedicated permission pair, not the generic
            # `folder.read`/`.write` the broad RBAC retrofit (ADR 0149)
            # added to "everyone" - see
            # `_require_folder_document_reference_permission`'s docstring
            # in folder-service/main.py for why (granting the generic pair
            # here would silently no-op against the actual gate).
            "permissions": ["folder.document_reference.write", "folder.document_reference.read"],
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


def _add_folder_document_reference(folder_id: str, document_id: str, *, added_by: str) -> None:
    response = httpx.post(
        f"{FOLDER_SERVICE_URL}/folders/{folder_id}/document-references",
        json={"document_id": document_id, "added_by": added_by},
        headers={"X-DMS-Principal": added_by},
        timeout=30.0,
    )
    response.raise_for_status()


def _remove_folder_document_reference(folder_id: str, document_id: str, *, removed_by: str) -> None:
    response = httpx.request(
        "DELETE",
        f"{FOLDER_SERVICE_URL}/folders/{folder_id}/document-references/{document_id}",
        json={"removed_by": removed_by},
        headers={"X-DMS-Principal": removed_by},
        timeout=30.0,
    )
    response.raise_for_status()


async def _poll_until(predicate, timeout_seconds=10.0, interval=0.2) -> bool:
    elapsed = 0.0
    while elapsed < timeout_seconds:
        if await predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


def test_document_created_event_triggers_indexing():
    document_id = _upload_document(filename=f"brief-{uuid.uuid4().hex[:8]}.txt")

    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)

    async def _indexed() -> bool:
        async with session_factory() as session:
            return await repository.get_document(session, document_id) is not None

    with TestClient(app):
        found = asyncio.run(_poll_until(_indexed, timeout_seconds=30.0))

    asyncio.run(engine.dispose())
    assert found, "Dokument wurde nicht rechtzeitig indiziert"


async def test_text_update_handler_creates_row_from_scratch_without_prior_document_event():
    """Regressionstest für die Cross-Stream-Backfill-Race (siehe pipeline.py-
    Docstring): der Text-Update-Handler darf sich nicht darauf verlassen,
    dass der Dokument-Handler das Dokument bereits indiziert hat - er muss
    auch dann einen vollständigen, korrekten Eintrag erzeugen, wenn er als
    erstes Event für ein Dokument eintrifft."""
    document_id = _upload_document(filename=f"scan-{uuid.uuid4().hex[:8]}.txt")

    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)
    document_client = DocumentServiceClient(DOCUMENT_SERVICE_URL)
    folder_client = FolderServiceClient(
        os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")
    )
    ocr_client = OcrServiceClient(os.environ.get("TEST_OCR_SERVICE_URL", "http://localhost:8012"))
    rendering_client = RenderingServiceClient(
        os.environ.get("TEST_RENDERING_SERVICE_URL", "http://localhost:8011")
    )

    handler = make_text_update_handler(
        session_factory=session_factory,
        document_client=document_client,
        folder_client=folder_client,
        ocr_client=ocr_client,
        rendering_client=rendering_client,
    )

    async with session_factory() as session:
        assert await repository.get_document(session, document_id) is None

    event = Event(
        event_type="ocr.completed",
        service_name="ocr-service-tests",
        subject=document_id,
        payload={
            "version_number": 1,
            "status": "ready",
            "engine": "tesseract",
            "average_confidence": 90.0,
        },
    )
    try:
        await handler(event.to_bytes())
    finally:
        await document_client.close()
        await folder_client.close()
        await ocr_client.close()
        await rendering_client.close()
        await engine.dispose()

    engine2 = build_engine(DSN)
    session_factory2 = make_session_factory(engine2)
    async with session_factory2() as session:
        indexed = await repository.get_document(session, document_id)
    await engine2.dispose()

    assert indexed is not None
    assert indexed.document_id == document_id


def test_folder_document_reference_added_event_triggers_indexing():
    """Real end-to-end: folder-service's `POST .../document-references`
    publishes the real (now `added_at`-enriched) event, search-service's
    live consumer picks it up (ADR 0118/0146)."""
    principal = f"hf-tester-{uuid.uuid4().hex[:8]}"
    folder_id = _create_folder(f"Handakte-{uuid.uuid4().hex[:8]}")
    _grant_folder_write(principal, folder_id)
    document_id = _upload_document(filename=f"beleg-{uuid.uuid4().hex[:8]}.txt")

    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)

    async def _indexed() -> bool:
        async with session_factory() as session:
            rows = await repository.list_folder_references(
                session, folder_id=folder_id, document_id=document_id, limit=1, offset=0
            )
            return len(rows) == 1

    with TestClient(app):
        _add_folder_document_reference(folder_id, document_id, added_by=principal)
        found = asyncio.run(_poll_until(_indexed, timeout_seconds=30.0))

    asyncio.run(engine.dispose())
    assert found, "Handakte-Referenz wurde nicht rechtzeitig indiziert"


def test_folder_document_reference_removed_event_deletes_index_row():
    principal = f"hf-tester-{uuid.uuid4().hex[:8]}"
    folder_id = _create_folder(f"Handakte-{uuid.uuid4().hex[:8]}")
    _grant_folder_write(principal, folder_id)
    document_id = _upload_document(filename=f"beleg-{uuid.uuid4().hex[:8]}.txt")

    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)

    async def _indexed() -> bool:
        async with session_factory() as session:
            rows = await repository.list_folder_references(
                session, folder_id=folder_id, document_id=document_id, limit=1, offset=0
            )
            return len(rows) == 1

    async def _removed_from_index() -> bool:
        async with session_factory() as session:
            rows = await repository.list_folder_references(
                session, folder_id=folder_id, document_id=document_id, limit=1, offset=0
            )
            return len(rows) == 0

    # Both polls (and the engine disposal) MUST share a single `asyncio.run`
    # call - asyncpg connections are bound to the event loop they were
    # created in, so a second, separate `asyncio.run` reusing the same
    # pooled `session_factory` raises "attached to a different loop". The
    # remove call itself ALSO belongs inside this one coroutine, fired only
    # once the add is confirmed indexed - firing both HTTP calls eagerly
    # up front raced the two events against the poll's own first check: on
    # a fast run, both add and remove could already be fully processed
    # before polling ever started, so `_indexed` (looking for the
    # NOW-ALREADY-GONE `rows==1` state) would loop until timeout and never
    # even reach the removal check - a real bug in this test, not in the
    # consumer (confirmed via a temporary handler-side print: both events
    # were processed correctly and near-instantly, the poll just started
    # too late to ever observe the intermediate "added" state).
    async def _wait_for_add_then_remove() -> bool:
        added = await _poll_until(_indexed, timeout_seconds=30.0)
        if not added:
            return False
        _remove_folder_document_reference(folder_id, document_id, removed_by=principal)
        return await _poll_until(_removed_from_index, timeout_seconds=30.0)

    with TestClient(app):
        _add_folder_document_reference(folder_id, document_id, added_by=principal)
        gone = asyncio.run(_wait_for_add_then_remove())

    asyncio.run(engine.dispose())
    assert gone, "Handakte-Referenz wurde nach Entfernen nicht rechtzeitig aus dem Index gelöscht"
