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


def _upload_document(*, filename: str) -> str:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": filename, "created_by": "search-service-tests"},
        files={"file": (filename, b"Inhalt", "text/plain")},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["id"]


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
    folder_client = FolderServiceClient(os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008"))
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
