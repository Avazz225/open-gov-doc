import os
import uuid

from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event
from rendering_service import repository
from rendering_service.consumer import make_ocr_handler
from rendering_service.storage_client import StorageClient

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
STORAGE_SERVICE_URL = os.environ.get("TEST_STORAGE_SERVICE_URL", "http://localhost:8005")


class FakeOcrClient:
    def __init__(self, full_text: str | None) -> None:
        self.full_text = full_text
        self.calls: list[tuple[str, int]] = []

    async def get_full_text(self, document_id: str, version_number: int) -> str | None:
        self.calls.append((document_id, version_number))
        return self.full_text


class EventRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    async def __call__(self, event_type: str, subject: str, payload: dict) -> None:
        self.events.append((event_type, subject, payload))


def _make_event(event_type: str, document_id: str | None, payload: dict) -> bytes:
    return Event(
        event_type=event_type, service_name="ocr-service", subject=document_id, payload=payload
    ).to_bytes()


async def test_ocr_handler_ignores_non_completed_events():
    ocr_client = FakeOcrClient(full_text="sollte nie abgerufen werden")
    engine = build_engine(DSN)
    handler = make_ocr_handler(
        session_factory=make_session_factory(engine),
        ocr_client=ocr_client,
        storage=StorageClient(STORAGE_SERVICE_URL),
        publish_event=EventRecorder(),
    )

    await handler(_make_event("ocr.failed", "doc-1", {"version_number": 1, "error": "kaputt"}))

    assert ocr_client.calls == []
    await engine.dispose()


async def test_ocr_handler_creates_rendition_when_missing():
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    ocr_client = FakeOcrClient(full_text="Erkannter OCR-Text")
    recorder = EventRecorder()
    engine = build_engine(DSN)
    handler = make_ocr_handler(
        session_factory=make_session_factory(engine),
        ocr_client=ocr_client,
        storage=StorageClient(STORAGE_SERVICE_URL),
        publish_event=recorder,
    )

    await handler(
        _make_event(
            "ocr.completed",
            document_id,
            {
                "version_number": 1,
                "status": "ready",
                "engine": "tesseract",
                "average_confidence": 90.0,
            },
        )
    )

    assert ocr_client.calls == [(document_id, 1)]
    assert len(recorder.events) == 1
    assert recorder.events[0][0] == "rendering.completed"
    await engine.dispose()


async def test_ocr_handler_skips_if_rendition_already_exists():
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        await repository.upsert_rendition(
            session,
            document_id=document_id,
            version_number=1,
            rendition_type="substitute_text",
            source_filename="brief.docx",
            source_content_type=None,
            target_filename="brief.txt",
            target_content_type="text/plain",
            size_bytes=5,
            storage_object_key=f"renditions/{document_id}/1/substitute_text",
            status="ready",
            error_message=None,
        )
        await session.commit()

    ocr_client = FakeOcrClient(full_text="sollte nicht abgerufen werden")
    recorder = EventRecorder()
    handler = make_ocr_handler(
        session_factory=session_factory,
        ocr_client=ocr_client,
        storage=StorageClient(STORAGE_SERVICE_URL),
        publish_event=recorder,
    )

    await handler(
        _make_event(
            "ocr.completed",
            document_id,
            {
                "version_number": 1,
                "status": "ready",
                "engine": "tesseract",
                "average_confidence": 90.0,
            },
        )
    )

    assert ocr_client.calls == []
    assert recorder.events == []
    await engine.dispose()
