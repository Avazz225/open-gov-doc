import asyncio
import os
import uuid
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient
from ocr_service.main import app
from reportlab.pdfgen import canvas

DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _text_pdf(text: str) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    c.drawString(20, 250, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def _upload_document(*, filename: str, content: bytes, content_type: str) -> str:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": filename, "created_by": "ocr-service-tests"},
        files={"file": (filename, content, content_type)},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["id"]


async def _poll_until(predicate, timeout_seconds=10.0, interval=0.2) -> bool:
    elapsed = 0.0
    while elapsed < timeout_seconds:
        if predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


def test_document_created_event_triggers_ocr(client):
    document_id = _upload_document(
        filename=f"brief-{uuid.uuid4().hex[:8]}.pdf",
        content=_text_pdf("Sehr geehrte Damen und Herren"),
        content_type="application/pdf",
    )

    def _ready() -> bool:
        body = client.get("/ocr-results", params={"document_id": document_id}).json()
        return any(r["status"] == "ready" for r in body)

    found = asyncio.run(_poll_until(_ready))
    assert found, "OCR-Event wurde nicht rechtzeitig verarbeitet"

    results = client.get("/ocr-results", params={"document_id": document_id}).json()
    assert len(results) == 1
    assert results[0]["engine"] == "native_text_layer"
    assert results[0]["version_number"] == 1
