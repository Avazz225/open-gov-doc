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
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as c:
        yield c


def _text_pdf(text: str) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    c.drawString(20, 250, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def _multi_page_pdf(page_count: int) -> bytes:
    # Text muss über `min_native_text_chars` (Default 20) liegen, sonst
    # entscheidet `select_engine()`, dass kein nutzbarer Textlayer vorliegt,
    # und wählt Tesseract statt `native_text_layer` - Tesseract ist auf dem
    # Host, der diese Tests außerhalb von Docker ausführt, nicht installiert.
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    for i in range(page_count):
        c.drawString(20, 250, f"Dies ist die Testseite Nummer {i + 1}")
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


def test_page_image_endpoint_returns_a_distinct_image_per_pdf_page(client):
    """Bugfix: die Vorschau eines mehrseitigen PDFs zeigte bislang immer nur
    Seite 1, weil die OCR-Pipeline ausschließlich ein einziges Seitenbild
    (fest "page-1.png") erzeugte, egal wie viele Seiten das PDF hatte."""
    document_id = _upload_document(
        filename=f"akte-{uuid.uuid4().hex[:8]}.pdf",
        content=_multi_page_pdf(3),
        content_type="application/pdf",
    )

    def _ready() -> bool:
        body = client.get("/ocr-results", params={"document_id": document_id}).json()
        return any(r["status"] == "ready" for r in body)

    found = asyncio.run(_poll_until(_ready))
    assert found, "OCR-Event wurde nicht rechtzeitig verarbeitet"

    result = client.get("/ocr-results", params={"document_id": document_id}).json()[0]
    assert len(result["pages"]) == 3
    ocr_result_id = result["id"]

    page_1 = client.get(f"/ocr-results/{ocr_result_id}/page-image", params={"page_number": 1})
    page_2 = client.get(f"/ocr-results/{ocr_result_id}/page-image", params={"page_number": 2})
    page_3 = client.get(f"/ocr-results/{ocr_result_id}/page-image", params={"page_number": 3})
    assert page_1.status_code == 200
    assert page_2.status_code == 200
    assert page_3.status_code == 200
    # Unterschiedliche Seiteninhalte ("Seite 1"/"Seite 2"/"Seite 3") müssen zu
    # unterschiedlichen gerasterten Bildern führen, nicht dreimal demselben.
    assert page_1.content != page_2.content
    assert page_2.content != page_3.content

    missing_page = client.get(f"/ocr-results/{ocr_result_id}/page-image", params={"page_number": 4})
    assert missing_page.status_code == 404
