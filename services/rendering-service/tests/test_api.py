import uuid
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfReader
from rendering_service import repository
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


def test_retry_returns_404_for_unknown_rendition():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post("/renditions/unbekannt:1:thumbnail/retry")
    assert response.status_code == 404


async def test_retry_returns_409_for_a_still_retryable_rendition(session):
    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    await repository.record_failure(
        session,
        document_id=document_id,
        version_number=1,
        rendition_type="pdf_archive",
        source_filename="a.pdf",
        source_content_type="application/pdf",
        error="e",
        max_attempts=5,
    )
    await session.commit()

    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(f"/renditions/{document_id}:1:pdf_archive/retry")

    assert response.status_code == 409


async def test_retry_for_a_permanently_missing_document_resets_attempts_but_stays_failed_permanent(
    session,
):
    """Post-Roadmap Phase 20 Session 4 (ADR 0080): der Endpunkt setzt
    `attempts` IMMER zuerst zurück (`repository.reset_for_retry`), bevor er
    `retry_rendition` erneut aufruft - sonst würde `record_failure` von der
    bereits erschöpften `attempts`-Zahl weiterzählen und eine
    `failed_permanent`-Rendition könnte nie wieder herauskommen (bei der
    Live-Verifikation dieser Session als echter Bug in ocr-service gefunden
    und hier vorsorglich gleich mitbehoben). Ein `document_id`, das gar
    nicht (mehr) existiert, lässt `retry_rendition` danach still mit
    `DocumentNotFoundError` abbrechen - `status` bleibt deshalb bei
    `failed_permanent` stehen, aber `attempts` ist bereits zurückgesetzt.
    Bewusst kein Test mit einem echten hochgeladenen Dokument: dessen
    `document.created`-Event würde vom in `TestClient(app)` startenden
    echten NATS-Konsumenten unabhängig verarbeitet und mit diesem Testaufruf
    um die `attempts`-Buchführung konkurrieren (nicht deterministisch)."""
    document_id = f"gone-{uuid.uuid4().hex[:8]}"
    await repository.record_failure(
        session,
        document_id=document_id,
        version_number=1,
        rendition_type="pdf_archive",
        source_filename="a.pdf",
        source_content_type="application/pdf",
        error="e",
        max_attempts=1,
    )
    await session.commit()

    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(f"/renditions/{document_id}:1:pdf_archive/retry")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed_permanent"
    assert body["attempts"] == 0
    assert body["error_message"] is None


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
