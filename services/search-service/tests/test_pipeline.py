import os
import uuid

import httpx
from dms_db_base import build_engine, make_session_factory
from search_service import repository
from search_service.document_client import DocumentServiceClient
from search_service.folder_client import FolderServiceClient
from search_service.pipeline import reindex_document

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
FOLDER_SERVICE_URL = os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")


class FakeTextClient:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.calls: list[tuple[str, int]] = []

    async def get_full_text(self, document_id: str, version_number: int) -> str | None:
        self.calls.append((document_id, version_number))
        return self.text

    async def get_substitute_text(self, document_id: str, version_number: int) -> str | None:
        self.calls.append((document_id, version_number))
        return self.text


def _create_folder(name: str) -> str:
    response = httpx.post(
        f"{FOLDER_SERVICE_URL}/folders",
        json={"name": name, "parent_id": "root", "created_by": "search-service-tests"},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["id"]


def _upload_document(*, filename: str, folder_id: str | None = None) -> str:
    data = {"title": filename, "created_by": "search-service-tests"}
    if folder_id is not None:
        data["folder_id"] = folder_id
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data=data,
        files={"file": (filename, b"Inhalt", "text/plain")},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["id"]


def _checkin_version(document_id: str, *, filename: str) -> None:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "search-service-tests"},
        files={"file": (filename, b"Inhalt v2", "text/plain")},
        timeout=30.0,
    )
    response.raise_for_status()


def _delete_document(document_id: str) -> None:
    response = httpx.delete(
        f"{DOCUMENT_SERVICE_URL}/documents/{document_id}",
        params={"deleted_by": "search-service-tests"},
        timeout=30.0,
    )
    response.raise_for_status()


async def _run_reindex(
    document_id: str, *, ocr_text: str | None = None, rendering_text: str | None = None
):
    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)
    document_client = DocumentServiceClient(DOCUMENT_SERVICE_URL)
    folder_client = FolderServiceClient(FOLDER_SERVICE_URL)
    ocr_client = FakeTextClient(ocr_text)
    rendering_client = FakeTextClient(rendering_text)
    try:
        await reindex_document(
            document_id,
            session_factory=session_factory,
            document_client=document_client,
            folder_client=folder_client,
            ocr_client=ocr_client,
            rendering_client=rendering_client,
        )
    finally:
        await document_client.close()
        await folder_client.close()
        await engine.dispose()

    async with session_factory() as session:
        return await repository.get_document(session, document_id)


async def test_reindex_document_denormalizes_folder_name():
    folder_id = _create_folder(f"Ordner-{uuid.uuid4().hex[:8]}")
    document_id = _upload_document(
        filename=f"brief-{uuid.uuid4().hex[:8]}.txt", folder_id=folder_id
    )

    indexed = await _run_reindex(document_id)

    assert indexed is not None
    assert indexed.folder_id == folder_id
    assert indexed.folder_name is not None


async def test_reindex_document_uses_ocr_text_when_available():
    document_id = _upload_document(filename=f"scan-{uuid.uuid4().hex[:8]}.txt")

    indexed = await _run_reindex(document_id, ocr_text="Erkannter Text")

    assert indexed.full_text == "Erkannter Text"


async def test_reindex_document_resets_full_text_on_version_change():
    document_id = _upload_document(filename=f"vertrag-{uuid.uuid4().hex[:8]}.txt")
    first = await _run_reindex(document_id, ocr_text="Text Version 1")
    assert first.full_text == "Text Version 1"

    _checkin_version(document_id, filename=f"vertrag-v2-{uuid.uuid4().hex[:8]}.txt")
    second = await _run_reindex(document_id, ocr_text=None, rendering_text=None)

    assert second.current_version_number == 2
    assert second.full_text == ""


async def test_reindex_document_removes_row_for_deleted_document():
    document_id = _upload_document(filename=f"geloescht-{uuid.uuid4().hex[:8]}.txt")
    await _run_reindex(document_id)

    _delete_document(document_id)
    result = await _run_reindex(document_id)

    assert result is None


async def test_reindex_document_returns_none_for_unknown_document():
    result = await _run_reindex(f"unbekannt-{uuid.uuid4().hex[:8]}")

    assert result is None
