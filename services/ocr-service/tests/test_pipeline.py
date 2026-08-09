import os
import shutil
import uuid
from io import BytesIO

import fitz
import httpx
import pytest
from dms_db_base import build_engine, make_session_factory
from ocr_service import pipeline, repository
from ocr_service.document_client import DocumentServiceClient
from ocr_service.storage_client import StorageClient
from PIL import Image
from reportlab.pdfgen import canvas

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
STORAGE_SERVICE_URL = os.environ.get("TEST_STORAGE_SERVICE_URL", "http://localhost:8005")

_TESSERACT_AVAILABLE = shutil.which("tesseract") is not None


def _upload_document(*, filename: str, content: bytes, content_type: str) -> str:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": filename, "created_by": "ocr-service-tests"},
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


def _text_pdf(text: str) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    c.drawString(20, 250, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def _blank_image(color=(255, 255, 255)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (300, 200), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _scanned_pdf(text: str) -> bytes:
    """Baut eine PDF, deren einziger Seiteninhalt ein eingebettetes Bild mit
    Text ist (kein Vektortext) - simuliert einen echten Scan ohne nutzbaren
    Textlayer, damit `select_engine()` `TesseractEngine` statt
    `NativeTextLayerEngine` wählt."""
    from PIL import ImageDraw

    image = Image.new("RGB", (800, 400), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((40, 150), text, fill=(0, 0, 0))
    img_buf = BytesIO()
    image.save(img_buf, format="PNG")

    doc = fitz.open()
    page = doc.new_page(width=800, height=400)
    page.insert_image(fitz.Rect(0, 0, 800, 400), stream=img_buf.getvalue())
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _list_versions(document_id: str) -> list[dict]:
    response = httpx.get(f"{DOCUMENT_SERVICE_URL}/documents/{document_id}/versions")
    response.raise_for_status()
    return response.json()


def _download_version_content(document_id: str, version_number: int) -> bytes:
    response = httpx.get(
        f"{DOCUMENT_SERVICE_URL}/documents/{document_id}/versions/{version_number}/content"
    )
    response.raise_for_status()
    return response.content


class EventRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    async def __call__(
        self, event_type: str, subject: str, payload: dict, actor: str | None = None
    ) -> None:
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


async def test_process_version_uses_native_text_layer_for_text_pdf():
    document_id = _upload_document(
        filename=f"brief-{uuid.uuid4().hex[:8]}.pdf",
        content=_text_pdf("Sehr geehrte Damen und Herren"),
        content_type="application/pdf",
    )
    recorder = EventRecorder()

    result = await _run_pipeline(document_id, 1, recorder)

    assert result is not None
    assert result.status == "ready"
    assert result.engine == "native_text_layer"
    assert "Sehr" in result.full_text
    assert recorder.events == [
        (
            "ocr.completed",
            document_id,
            {
                "version_number": 1,
                "status": "ready",
                "engine": "native_text_layer",
                "average_confidence": 100.0,
            },
        )
    ]


async def test_process_version_skips_unsupported_format():
    document_id = _upload_document(
        filename=f"daten-{uuid.uuid4().hex[:8]}.csv",
        content=b"a,b,c\n1,2,3",
        content_type="text/csv",
    )
    recorder = EventRecorder()

    result = await _run_pipeline(document_id, 1, recorder)

    assert result is None
    assert recorder.events == []


async def test_process_version_marks_corrupt_pdf_as_failed():
    document_id = _upload_document(
        filename=f"kaputt-{uuid.uuid4().hex[:8]}.pdf",
        content=b"das ist kein echtes PDF",
        content_type="application/pdf",
    )
    recorder = EventRecorder()

    result = await _run_pipeline(document_id, 1, recorder)

    assert result is not None
    assert result.status == "failed"
    assert result.error_message is not None
    assert recorder.events == [
        ("ocr.failed", document_id, {"version_number": 1, "error": result.error_message})
    ]


async def test_process_version_skips_gracefully_if_storage_object_missing():
    """Regressionstest analog zu rendering-service: existiert die Version beim
    Document Service, aber ist ihr Inhalt im Storage Service nicht (mehr)
    auffindbar, darf die Pipeline nicht mit einer unbehandelten Exception
    abbrechen - sonst würde die zugehörige NATS-Nachricht nie geackt und
    endlos redelivert."""
    document_id = _upload_document(
        filename=f"verwaist-{uuid.uuid4().hex[:8]}.pdf",
        content=b"%PDF-1.4 Platzhalter",
        content_type="application/pdf",
    )
    _delete_storage_object_for_version(document_id, 1)

    recorder = EventRecorder()
    result = await _run_pipeline(document_id, 1, recorder)

    assert result is None
    assert recorder.events == []


async def test_process_version_returns_none_for_unknown_document():
    recorder = EventRecorder()

    result = await _run_pipeline(f"unbekannt-{uuid.uuid4().hex[:8]}", 1, recorder)

    assert result is None
    assert recorder.events == []


async def test_process_version_skips_when_over_configured_word_limit():
    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        await repository.update_config(
            session, max_word_count=10, batch_size=4, allowed_content_types=[]
        )
        await session.commit()
    await engine.dispose()

    document_id = _upload_document(
        filename=f"lang-{uuid.uuid4().hex[:8]}.pdf",
        content=_text_pdf("Sehr geehrte Damen und Herren"),
        content_type="application/pdf",
    )
    recorder = EventRecorder()

    result = await _run_pipeline(document_id, 1, recorder)

    assert result is not None
    assert result.status == "skipped"
    assert result.error_message is not None
    assert recorder.events == [
        ("ocr.skipped", document_id, {"version_number": 1, "estimated_words": 250})
    ]


async def test_process_version_skips_content_type_not_on_allowlist():
    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        await repository.update_config(
            session, max_word_count=None, batch_size=4, allowed_content_types=["image/png"]
        )
        await session.commit()
    await engine.dispose()

    document_id = _upload_document(
        filename=f"nicht-erlaubt-{uuid.uuid4().hex[:8]}.pdf",
        content=_text_pdf("Sehr geehrte Damen und Herren"),
        content_type="application/pdf",
    )
    recorder = EventRecorder()

    result = await _run_pipeline(document_id, 1, recorder)

    assert result is not None
    assert result.status == "skipped"
    assert "application/pdf" in result.error_message
    assert recorder.events == [
        ("ocr.skipped", document_id, {"version_number": 1, "content_type": "application/pdf"})
    ]


async def test_process_version_runs_when_content_type_on_allowlist():
    engine = build_engine(DSN)
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        await repository.update_config(
            session, max_word_count=None, batch_size=4, allowed_content_types=["application/pdf"]
        )
        await session.commit()
    await engine.dispose()

    document_id = _upload_document(
        filename=f"erlaubt-{uuid.uuid4().hex[:8]}.pdf",
        content=_text_pdf("Sehr geehrte Damen und Herren"),
        content_type="application/pdf",
    )
    recorder = EventRecorder()

    result = await _run_pipeline(document_id, 1, recorder)

    assert result is not None
    assert result.status == "ready"


@pytest.mark.skipif(
    not _TESSERACT_AVAILABLE,
    reason="tesseract-ocr nicht auf diesem Host installiert - Verifikation erfolgt "
    "im echten Docker-Container",
)
async def test_process_version_uses_tesseract_for_raster_image():
    document_id = _upload_document(
        filename=f"scan-{uuid.uuid4().hex[:8]}.png",
        content=_blank_image(),
        content_type="image/png",
    )
    recorder = EventRecorder()

    result = await _run_pipeline(document_id, 1, recorder)

    assert result is not None
    assert result.engine == "tesseract"
    # Ein leeres weißes Bild hat keine erkennbaren Wörter -> Konfidenz 0.0,
    # damit unterhalb der needs_review-Schwelle.
    assert result.status == "needs_review"


@pytest.mark.skipif(
    not _TESSERACT_AVAILABLE,
    reason="tesseract-ocr nicht auf diesem Host installiert - Verifikation erfolgt "
    "im echten Docker-Container",
)
async def test_process_version_embeds_text_layer_and_creates_new_version_for_scanned_pdf():
    """Nutzer-Feedback: ein Scan ohne Textlayer soll eine neue, durchsuchbare
    Version bekommen (Original bleibt als reiner Scan erhalten) - analog zum
    bereits etablierten signature-service-Muster (ADR 0025)."""
    document_id = _upload_document(
        filename=f"scan-{uuid.uuid4().hex[:8]}.pdf",
        content=_scanned_pdf("TESTDOKUMENT"),
        content_type="application/pdf",
    )
    recorder = EventRecorder()

    result = await _run_pipeline(document_id, 1, recorder)

    assert result is not None
    assert result.engine == "tesseract"  # Original hatte keinen nutzbaren Textlayer

    versions = _list_versions(document_id)
    assert len(versions) == 2
    new_version = next(v for v in versions if v["version_number"] == 2)
    assert new_version["created_by"] == "system:ocr-service"
    assert new_version["comment"] == "OCR: durchsuchbarer Textlayer eingebettet"

    new_content = _download_version_content(document_id, 2)
    doc = fitz.open(stream=new_content, filetype="pdf")
    try:
        extracted = doc[0].get_text("text").strip()
    finally:
        doc.close()
    assert extracted != ""  # der eingebettete Textlayer ist jetzt extrahierbar


@pytest.mark.skipif(
    not _TESSERACT_AVAILABLE,
    reason="tesseract-ocr nicht auf diesem Host installiert - Verifikation erfolgt "
    "im echten Docker-Container",
)
async def test_process_version_does_not_create_new_version_for_blank_scan_without_words():
    """Realer Bug, beim Testen entdeckt: eine leere Scan-PDF ohne erkennbaren
    Text bekam trotzdem eine neue (sinnlose) Version mit leerem Textlayer -
    das störte Versionsnummern-Annahmen in völlig anderen Services' Tests
    (z. B. signature-service), da der ocr-service-Container während deren
    Testläufen live weiterläuft und jedes hochgeladene PDF verarbeitet."""
    document_id = _upload_document(
        filename=f"leer-{uuid.uuid4().hex[:8]}.pdf",
        content=_scanned_pdf(""),
        content_type="application/pdf",
    )
    recorder = EventRecorder()

    result = await _run_pipeline(document_id, 1, recorder)

    assert result is not None
    assert result.engine == "tesseract"
    versions = _list_versions(document_id)
    assert len(versions) == 1


@pytest.mark.skipif(
    not _TESSERACT_AVAILABLE,
    reason="tesseract-ocr nicht auf diesem Host installiert - Verifikation erfolgt "
    "im echten Docker-Container",
)
async def test_process_version_does_not_reembed_its_own_previously_created_version():
    """Realer Bug, beim Testen entdeckt: die neu erzeugte Version löst selbst
    wieder `document.version.created` aus und wird erneut verarbeitet - ohne
    eine explizite Sperre würde sie (da die unsichtbar eingebetteten Wörter
    Tesseracts Pixel-basierte Erkennung nicht beeinflussen und `_native_text_
    available()`s Zeichen-Schwelle bei kurzem Text noch nicht greift) erneut
    per Tesseract erkannt und ein weiteres Mal eingebettet - unbegrenzt
    fortlaufend statt sich nach einem Durchlauf selbst zu beenden."""
    document_id = _upload_document(
        filename=f"scan-{uuid.uuid4().hex[:8]}.pdf",
        content=_scanned_pdf("KURZ"),  # bewusst kurzes Wort, unter min_native_text_chars
        content_type="application/pdf",
    )
    recorder = EventRecorder()

    first = await _run_pipeline(document_id, 1, recorder)
    assert first is not None
    versions_after_first = _list_versions(document_id)
    assert len(versions_after_first) == 2

    new_version_number = max(v["version_number"] for v in versions_after_first)
    second = await _run_pipeline(document_id, new_version_number, recorder)
    assert second is not None  # OCR-Ergebnis für die neue Version wird trotzdem gespeichert

    versions_after_second = _list_versions(document_id)
    assert len(versions_after_second) == 2  # keine dritte Version


@pytest.mark.skipif(
    not _TESSERACT_AVAILABLE,
    reason="tesseract-ocr nicht auf diesem Host installiert - Verifikation erfolgt "
    "im echten Docker-Container",
)
async def test_process_version_does_not_create_new_version_for_raster_image():
    """Für Bilder gibt es kein PDF-Textlayer-Konzept (Rückfrage bei
    Sessionstart) - das Bild bleibt unverändert die einzige Version, OCR-Text
    steht stattdessen separat über GET /ocr-results zur Verfügung."""
    document_id = _upload_document(
        filename=f"foto-{uuid.uuid4().hex[:8]}.png",
        content=_blank_image(),
        content_type="image/png",
    )
    recorder = EventRecorder()

    result = await _run_pipeline(document_id, 1, recorder)

    assert result is not None
    assert result.engine == "tesseract"
    versions = _list_versions(document_id)
    assert len(versions) == 1
