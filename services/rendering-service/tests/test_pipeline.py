import os
import uuid
from io import BytesIO

import httpx
from dms_db_base import build_engine, make_session_factory
from docx import Document
from PIL import Image
from rendering_service import pipeline
from rendering_service.document_client import DocumentServiceClient
from rendering_service.storage_client import StorageClient

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
STORAGE_SERVICE_URL = os.environ.get("TEST_STORAGE_SERVICE_URL", "http://localhost:8005")


def _upload_document(*, filename: str, content: bytes, content_type: str) -> str:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": filename, "created_by": "rendering-service-tests"},
        files={"file": (filename, content, content_type)},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["id"]


def _delete_storage_object_for_version(document_id: str, version_number: int) -> None:
    version = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents/{document_id}/versions/{version_number}")
    version.raise_for_status()
    checksum = version.json()["checksum_sha256"]
    response = httpx.delete(f"{STORAGE_SERVICE_URL}/objects/documents/{document_id}/{checksum}")
    response.raise_for_status()


def _real_png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (400, 300), color=(10, 200, 40)).save(buf, format="PNG")
    return buf.getvalue()


def _real_docx(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


class EventRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    async def __call__(self, event_type: str, subject: str, payload: dict) -> None:
        self.events.append((event_type, subject, payload))


async def _run_pipeline(document_id: str, version_number: int, recorder: EventRecorder):
    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)
    document_client = DocumentServiceClient(DOCUMENT_SERVICE_URL)
    storage = StorageClient(STORAGE_SERVICE_URL)
    try:
        return await pipeline.process_version(
            document_id,
            version_number,
            session_factory=session_factory,
            document_client=document_client,
            storage=storage,
            publish_event=recorder,
        )
    finally:
        await document_client.close()
        await storage.close()
        await engine.dispose()


async def test_process_version_generates_thumbnail_for_image():
    document_id = _upload_document(
        filename=f"foto-{uuid.uuid4().hex[:8]}.png", content=_real_png(), content_type="image/png"
    )
    recorder = EventRecorder()

    results = await _run_pipeline(document_id, 1, recorder)

    assert len(results) == 1
    assert results[0].rendition_type == "thumbnail"
    assert results[0].status == "ready"
    assert recorder.events == [
        ("rendering.completed", document_id, {
            "version_number": 1,
            "rendition_type": "thumbnail",
            "target_filename": results[0].target_filename,
            "status": "ready",
            "error": None,
        })
    ]

    storage = StorageClient(STORAGE_SERVICE_URL)
    try:
        data = await storage.download(results[0].storage_object_key)
        thumb = Image.open(BytesIO(data))
        assert thumb.width <= 256
    finally:
        await storage.close()


async def test_process_version_extracts_docx_text():
    document_id = _upload_document(
        filename=f"brief-{uuid.uuid4().hex[:8]}.docx",
        content=_real_docx("Sehr geehrte Damen und Herren"),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    recorder = EventRecorder()

    results = await _run_pipeline(document_id, 1, recorder)

    assert len(results) == 1
    assert results[0].rendition_type == "substitute_text"
    assert results[0].status == "ready"

    storage = StorageClient(STORAGE_SERVICE_URL)
    try:
        data = await storage.download(results[0].storage_object_key)
        assert b"Sehr geehrte Damen und Herren" in data
    finally:
        await storage.close()


async def test_process_version_skips_unsupported_format():
    document_id = _upload_document(
        filename=f"daten-{uuid.uuid4().hex[:8]}.csv",
        content=b"a,b,c\n1,2,3",
        content_type="text/csv",
    )
    recorder = EventRecorder()

    results = await _run_pipeline(document_id, 1, recorder)

    assert results == []
    assert recorder.events == []


async def test_process_version_marks_corrupt_pdf_as_failed():
    document_id = _upload_document(
        filename=f"kaputt-{uuid.uuid4().hex[:8]}.pdf",
        content=b"das ist kein echtes PDF",
        content_type="application/pdf",
    )
    recorder = EventRecorder()

    results = await _run_pipeline(document_id, 1, recorder)

    assert len(results) == 1
    assert results[0].rendition_type == "pdf_archive"
    assert results[0].status == "failed"
    assert results[0].error_message is not None
    assert recorder.events[0][2]["status"] == "failed"


async def test_process_version_skips_gracefully_if_storage_object_missing():
    """Regressionstest: existiert die Version beim Document Service, aber ist
    ihr Inhalt im Storage Service nicht (mehr) auffindbar (Inkonsistenz, siehe
    document_service.storage_client.ObjectNotFoundError), darf die Pipeline
    nicht mit einer unbehandelten Exception abbrechen - sonst würde die
    zugehörige NATS-Nachricht nie geackt und endlos redelivert (siehe
    consumer.py/pipeline.py)."""
    document_id = _upload_document(
        filename=f"verwaist-{uuid.uuid4().hex[:8]}.pdf",
        content=b"%PDF-1.4 Platzhalter",
        content_type="application/pdf",
    )
    _delete_storage_object_for_version(document_id, 1)

    recorder = EventRecorder()
    results = await _run_pipeline(document_id, 1, recorder)

    assert results == []
    assert recorder.events == []


async def test_process_version_returns_empty_for_unknown_document():
    recorder = EventRecorder()

    results = await _run_pipeline(f"unbekannt-{uuid.uuid4().hex[:8]}", 1, recorder)

    assert results == []
    assert recorder.events == []


async def _run_process_ocr_text(
    document_id: str, version_number: int, full_text: str, recorder: EventRecorder
):
    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)
    storage = StorageClient(STORAGE_SERVICE_URL)
    try:
        return await pipeline.process_ocr_text(
            document_id,
            version_number,
            full_text=full_text,
            session_factory=session_factory,
            storage=storage,
            publish_event=recorder,
        )
    finally:
        await storage.close()
        await engine.dispose()


async def test_process_ocr_text_creates_substitute_text_rendition():
    """Nachzieheffekt (P5-S3): OCR-Volltext für ein gescanntes Dokument, das
    hier mangels OCR keine substitute_text-Rendition bekommen hätte."""
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    recorder = EventRecorder()

    rendition = await _run_process_ocr_text(document_id, 1, "Erkannter OCR-Text", recorder)

    assert rendition is not None
    assert rendition.rendition_type == "substitute_text"
    assert rendition.status == "ready"
    assert recorder.events == [
        (
            "rendering.completed",
            document_id,
            {
                "version_number": 1,
                "rendition_type": "substitute_text",
                "target_filename": "ocr_text.txt",
                "status": "ready",
                "error": None,
            },
        )
    ]

    storage = StorageClient(STORAGE_SERVICE_URL)
    try:
        data = await storage.download(rendition.storage_object_key)
        assert data == b"Erkannter OCR-Text"
    finally:
        await storage.close()


async def test_process_ocr_text_skips_empty_text():
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    recorder = EventRecorder()

    rendition = await _run_process_ocr_text(document_id, 1, "   ", recorder)

    assert rendition is None
    assert recorder.events == []
