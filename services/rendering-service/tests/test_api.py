import json
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


def test_list_renditions_without_document_id_is_accepted():
    """Post-Roadmap Phase 20 Session 7: `document_id` ist jetzt optional -
    zuvor lieferte ein Aufruf ohne ihn `422` (Pflichtparameter)."""
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.get("/renditions", params={"status": "failed_permanent"})
    assert response.status_code == 200


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


def test_render_convert_to_pdf_passes_through_an_already_pdf_file():
    """Post-Roadmap Phase 28 (ADR 0107): the export feature's on-demand
    conversion endpoint, reusing PdfArchiveRenderer's dispatch."""
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/convert-to-pdf",
            files={"file": ("akte.pdf", _real_pdf(pages=2), "application/pdf")},
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) == 2


def test_render_convert_to_pdf_converts_a_text_file():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/convert-to-pdf",
            files={"file": ("notiz.txt", b"Hello world", "text/plain")},
        )
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_render_convert_to_pdf_rejects_unsupported_format():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/convert-to-pdf",
            files={"file": ("archiv.zip", b"PK\x03\x04", "application/zip")},
        )
    assert response.status_code == 422


# --- Document redaction (14.2, post-roadmap phase 31 session 4, ADR 0115) --


def test_render_pdf_page_count():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/pdf-page-count",
            files={"file": ("akte.pdf", _real_pdf(pages=3), "application/pdf")},
        )
    assert response.status_code == 200
    assert response.json() == {"page_count": 3}


def test_render_pdf_page_image_returns_png():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/pdf-page-image",
            data={"page_number": "1"},
            files={"file": ("akte.pdf", _real_pdf(pages=1), "application/pdf")},
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_render_pdf_page_image_rejects_out_of_range_page():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/pdf-page-image",
            data={"page_number": "99"},
            files={"file": ("akte.pdf", _real_pdf(pages=1), "application/pdf")},
        )
    assert response.status_code == 422


def test_render_redact_returns_pdf_with_content_removed():
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    c.drawString(10, 10, "GEHEIM")
    c.showPage()
    c.save()
    source = buf.getvalue()

    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/redact",
            data={
                "regions": json.dumps(
                    [{"page_number": 1, "x": 0.0, "y": 0.8, "width": 1.0, "height": 0.2}]
                )
            },
            files={"file": ("akte.pdf", source, "application/pdf")},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    reader = PdfReader(BytesIO(response.content))
    assert "GEHEIM" not in reader.pages[0].extract_text()


def test_render_redact_rejects_empty_regions():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/redact",
            data={"regions": "[]"},
            files={"file": ("akte.pdf", _real_pdf(pages=1), "application/pdf")},
        )
    assert response.status_code == 400


def test_render_redact_rejects_out_of_range_page():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/redact",
            data={
                "regions": json.dumps(
                    [{"page_number": 5, "x": 0.0, "y": 0.0, "width": 1.0, "height": 0.2}]
                )
            },
            files={"file": ("akte.pdf", _real_pdf(pages=1), "application/pdf")},
        )
    assert response.status_code == 422


def test_render_export_document_merges_document_and_history():
    """Post-Roadmap Phase 28 (ADR 0107)."""
    history = json.dumps(
        [{"happened_at": "2026-01-02T00:00:00Z", "actor": "alice", "action": "exported"}]
    )
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/export/document",
            data={"title": "Contract.pdf", "history_position": "after", "history": history},
            files={"file": ("contract.pdf", _real_pdf(pages=2), "application/pdf")},
        )
    assert response.status_code == 200
    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) == 3  # 2 document pages + 1 history page
    assert "alice" in reader.pages[2].extract_text()
    assert "Page 1/3" in reader.pages[0].extract_text()


def test_render_export_document_rejects_invalid_history_position():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/export/document",
            data={"title": "Contract.pdf", "history_position": "sideways", "history": "[]"},
            files={"file": ("contract.pdf", _real_pdf(pages=1), "application/pdf")},
        )
    assert response.status_code == 422


def test_render_export_folder_combines_documents_with_toc_and_bookmarks():
    """Post-Roadmap Phase 28 (ADR 0107)."""
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        doc_a = client.post(
            "/render/export/document",
            data={"title": "A.pdf", "history_position": "after", "history": "[]"},
            files={"file": ("a.pdf", _real_pdf(pages=1), "application/pdf")},
        ).content
        doc_b = client.post(
            "/render/export/document",
            data={"title": "B.pdf", "history_position": "after", "history": "[]"},
            files={"file": ("b.pdf", _real_pdf(pages=1), "application/pdf")},
        ).content

        response = client.post(
            "/render/export/folder",
            data={"titles": ["A.pdf", "B.pdf"]},
            files=[
                ("files", ("a.pdf", doc_a, "application/pdf")),
                ("files", ("b.pdf", doc_b, "application/pdf")),
            ],
        )
    assert response.status_code == 200
    reader = PdfReader(BytesIO(response.content))
    # 1 TOC page + (1 doc + 1 history) for A + (1 doc + 1 history) for B.
    assert len(reader.pages) == 5
    assert len(reader.outline) == 2


def test_render_export_folder_rejects_mismatched_titles_and_files():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as client:
        response = client.post(
            "/render/export/folder",
            data={"titles": ["A.pdf", "B.pdf"]},
            files=[("files", ("a.pdf", _real_pdf(pages=1), "application/pdf"))],
        )
    assert response.status_code == 422
