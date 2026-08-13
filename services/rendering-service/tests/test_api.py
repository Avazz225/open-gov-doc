from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfReader
from rendering_service.main import app
from reportlab.pdfgen import canvas


def _real_pdf(pages: int = 2) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    for i in range(pages):
        c.drawString(10, 100, f"Seite {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def test_healthz():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "rendering-service"


def test_list_renditions_empty_for_unknown_document():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.get("/renditions", params={"document_id": "unbekannt"})
    assert response.status_code == 200
    assert response.json() == []


def test_get_rendition_404():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.get("/renditions/unbekannt:1:thumbnail")
    assert response.status_code == 404


def test_download_rendition_content_404():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.get("/renditions/unbekannt:1:thumbnail/content")
    assert response.status_code == 404


def test_render_watermark_returns_stamped_pdf():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/watermark",
            data={"text": "VERTRAULICH"},
            files={"file": ("akte.pdf", _real_pdf(pages=2), "application/pdf")},
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) == 2


def test_render_watermark_rejects_garbage():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/watermark",
            data={"text": "VERTRAULICH"},
            files={"file": ("kaputt.pdf", b"kein pdf", "application/pdf")},
        )
    assert response.status_code == 400
