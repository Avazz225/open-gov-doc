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
        headers={"X-DMS-Principal": "ocr-service-tests"},
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
    """Phase 60 Session 1: `document_id` now needs the caller's own
    `document.read` (`_require_ocr_document_permission`), checked against a
    real `permission-service` `ResourceNode` - a completely made-up
    `document_id` is an UNREGISTERED resource and fails closed (`403`),
    same convention as everywhere else in this project. Uses a real,
    freshly uploaded document with no OCR result yet instead, which is
    what this test actually means to cover: an empty list for a document
    that legitimately has none, not an unregistered/nonexistent one."""
    document_id = _upload_corrupt_pdf()
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get("/ocr-results", params={"document_id": document_id})
    assert response.status_code == 200
    assert response.json() == []


def test_list_ocr_results_unregistered_document_is_403():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get("/ocr-results", params={"document_id": "unbekannt"})
    assert response.status_code == 403


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


async def test_get_ocr_result_without_document_permission_is_403(session):
    """RBAC (Phase 60 Session 1, ADR 0183) - `GET /ocr-results/{id}`
    previously checked only the coarse, "everyone"-granted `ocr.read`, so
    any caller could read ANY OCR result's full text/confidence/pages
    regardless of the underlying document's own ACL. An `OcrResult` row
    pointing at an unregistered `document_id` (never created, or fully
    purged) now fails closed at `403` once existence (the row itself) is
    confirmed - same existence-then-permission ordering as everywhere else
    in this project."""
    document_id = f"gone-{uuid.uuid4().hex[:8]}"
    result = await repository.record_failure(
        session, document_id=document_id, version_number=1, engine="", error="e", max_attempts=5
    )
    await session.commit()

    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get(f"/ocr-results/{result.id}")

    assert response.status_code == 403


async def test_download_page_image_without_document_permission_is_403(session):
    document_id = f"gone-{uuid.uuid4().hex[:8]}"
    result = await repository.record_failure(
        session, document_id=document_id, version_number=1, engine="", error="e", max_attempts=5
    )
    await session.commit()

    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get(f"/ocr-results/{result.id}/page-image")

    assert response.status_code == 403


async def test_retry_returns_409_for_a_still_retryable_result(session):
    document_id = _upload_corrupt_pdf()
    await repository.record_failure(
        session, document_id=document_id, version_number=1, engine="", error="e", max_attempts=5
    )
    await session.commit()

    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.post(f"/ocr-results/{document_id}:1/retry")

    assert response.status_code == 409


async def test_retry_for_an_unregistered_document_is_403(session):
    """Phase 60 Session 1 (ADR 0183) deliberately changes this test's
    outcome: `retry_ocr_result` now requires the caller's own
    `document.write` on `result.document_id` (`_require_ocr_document_
    permission`, mirroring `rendering-service`'s identical IDOR fix) -
    previously only the coarse, "everyone"-granted `ocr.write` was checked,
    so ANY caller could retry ANY OCR result regardless of their access to
    the underlying document. A `document_id` that was never registered in
    `permission-service` (never created via `document-service`, or already
    fully purged - `document.resource.deleted` removes its `ResourceNode`
    too) now fails closed at `403`, before `process_version` ever gets a
    chance to run its own graceful `DocumentNotFoundError` handling
    (previously reachable, `200`/`failed_permanent` with `attempts` reset,
    see git history for the pre-Phase-60-S1 version of this test). A
    permanently failed OCR result for a document with no permission record
    left at all can no longer be retried via this endpoint - accepted,
    since such a retry could never have succeeded anyway (nothing to OCR),
    only ever reset bookkeeping with no real effect."""
    document_id = f"gone-{uuid.uuid4().hex[:8]}"
    await repository.record_failure(
        session, document_id=document_id, version_number=1, engine="", error="e", max_attempts=1
    )
    await session.commit()

    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.post(f"/ocr-results/{document_id}:1/retry")

    assert response.status_code == 403


def test_mark_reviewed_returns_404_for_unknown_result():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.post("/ocr-results/unbekannt:1/reviewed", json={})
    assert response.status_code == 404


async def test_mark_reviewed_returns_409_for_a_result_not_needing_review(session):
    document_id = _upload_corrupt_pdf()
    result = await repository.upsert_ocr_result(
        session,
        document_id=document_id,
        version_number=1,
        status="ready",
        engine="native_text_layer",
        average_confidence=100.0,
        full_text="Hallo",
        pages=[],
        page_image_storage_key=None,
        error_message=None,
    )
    await session.commit()

    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.post(f"/ocr-results/{result.id}/reviewed", json={})

    assert response.status_code == 409


async def test_mark_reviewed_flips_status_to_ready_and_records_reviewer(session):
    """Deliberately no `X-DMS-Principal` header on the request client below
    (unlike every other test in this file) - this endpoint is meant to be
    reachable exactly the way `workflow-service`'s `_handle_connector_task`
    calls it: no principal header at all (Phase 45 Session 3, see the
    endpoint's own docstring)."""
    document_id = _upload_corrupt_pdf()
    result = await repository.upsert_ocr_result(
        session,
        document_id=document_id,
        version_number=1,
        status="needs_review",
        engine="tesseract",
        average_confidence=40.0,
        full_text="unklar",
        pages=[],
        page_image_storage_key=None,
        error_message=None,
    )
    await session.commit()

    with TestClient(app) as client:
        response = client.post(
            f"/ocr-results/{result.id}/reviewed",
            json={
                "reviewed_by": "alice",
                "document_id": document_id,
                "ocr_result_id": result.id,
                "average_confidence": 40.0,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["reviewed_by"] == "alice"
    assert body["reviewed_at"] is not None


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
