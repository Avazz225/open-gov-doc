import os
import uuid

import httpx
from fastapi.testclient import TestClient
from ocr_service import repository
from ocr_service.main import app

DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")


def _upload_corrupt_pdf() -> str:
    filename = f"kaputt-{uuid.uuid4().hex[:8]}.pdf"
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": filename, "created_by": "ocr-service-tests"},
        files={"file": (filename, b"das ist kein echtes PDF", "application/pdf")},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["id"]


def test_healthz():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "ocr-service"


def test_list_ocr_results_empty_for_unknown_document():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get("/ocr-results", params={"document_id": "unbekannt"})
    assert response.status_code == 200
    assert response.json() == []


def test_list_ocr_results_without_document_id_is_accepted():
    """Post-Roadmap Phase 20 Session 7: `document_id` ist jetzt optional -
    zuvor lieferte ein Aufruf ohne ihn `422` (Pflichtparameter)."""
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get("/ocr-results", params={"status": "failed_permanent"})
    assert response.status_code == 200


def test_get_ocr_result_404():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get("/ocr-results/unbekannt:1")
    assert response.status_code == 404


def test_download_page_image_404_for_unknown_result():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get("/ocr-results/unbekannt:1/page-image")
    assert response.status_code == 404


def test_retry_returns_404_for_unknown_result():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.post("/ocr-results/unbekannt:1/retry")
    assert response.status_code == 404


async def test_retry_returns_409_for_a_still_retryable_result(session):
    document_id = _upload_corrupt_pdf()
    await repository.record_failure(
        session, document_id=document_id, version_number=1, engine="", error="e", max_attempts=5
    )
    await session.commit()

    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.post(f"/ocr-results/{document_id}:1/retry")

    assert response.status_code == 409


async def test_retry_for_a_permanently_missing_document_resets_attempts_but_stays_failed_permanent(
    session,
):
    """Post-Roadmap Phase 20 Session 4 (ADR 0080): der Endpunkt setzt
    `attempts` IMMER zuerst zurück (`repository.reset_for_retry`), bevor er
    `process_version` erneut aufruft - sonst würde `record_failure` von der
    bereits erschöpften `attempts`-Zahl weiterzählen und ein
    `failed_permanent`-Ergebnis könnte nie wieder herauskommen (bei der
    Live-Verifikation dieser Session als echter Bug gefunden). Ein
    `document_id`, das gar nicht (mehr) existiert, lässt `process_version`
    danach still mit `DocumentNotFoundError` abbrechen (kein Redelivery-
    Endlosschleifen-Risiko, siehe pipeline.py) - `status` bleibt deshalb bei
    `failed_permanent` stehen (kein Erfolg vorgetäuscht), aber `attempts` ist
    bereits zurückgesetzt. Bewusst kein Test mit einem echten hochgeladenen
    Dokument: dessen `document.created`-Event würde vom in `TestClient(app)`
    startenden echten NATS-Konsumenten unabhängig verarbeitet und mit diesem
    Testaufruf um die `attempts`-Buchführung konkurrieren (nicht
    deterministisch)."""
    document_id = f"gone-{uuid.uuid4().hex[:8]}"
    await repository.record_failure(
        session, document_id=document_id, version_number=1, engine="", error="e", max_attempts=1
    )
    await session.commit()

    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.post(f"/ocr-results/{document_id}:1/retry")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed_permanent"
    assert body["attempts"] == 0
    assert body["error_message"] is None


def test_get_config_returns_defaults_on_first_access():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get("/config")
    assert response.status_code == 200
    body = response.json()
    assert body["max_word_count"] is None
    assert body["batch_size"] == 4
    # Standardmäßig nur PDFs (Nutzer-Feedback) - Bilder erfordern eine
    # bewusste Admin-Freigabe über PUT /config.
    assert body["allowed_content_types"] == ["application/pdf"]


def test_put_config_updates_and_persists():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        put_response = client.put("/config", json={"max_word_count": 3000, "batch_size": 2})
        assert put_response.status_code == 200
        assert put_response.json()["max_word_count"] == 3000
        assert put_response.json()["batch_size"] == 2

        get_response = client.get("/config")
        assert get_response.json()["max_word_count"] == 3000
        assert get_response.json()["batch_size"] == 2


def test_put_config_rejects_batch_size_out_of_range():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.put("/config", json={"max_word_count": None, "batch_size": 0})
    assert response.status_code == 422


def test_put_config_persists_allowed_content_types():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        put_response = client.put(
            "/config",
            json={
                "max_word_count": None,
                "batch_size": 4,
                "allowed_content_types": ["image/tiff", "image/bmp"],
            },
        )
        assert put_response.status_code == 200
        assert put_response.json()["allowed_content_types"] == ["image/tiff", "image/bmp"]

        get_response = client.get("/config")
        assert get_response.json()["allowed_content_types"] == ["image/tiff", "image/bmp"]
