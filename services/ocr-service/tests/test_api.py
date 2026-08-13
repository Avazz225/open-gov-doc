from fastapi.testclient import TestClient
from ocr_service.main import app


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


def test_get_ocr_result_404():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get("/ocr-results/unbekannt:1")
    assert response.status_code == 404


def test_download_page_image_404_for_unknown_result():
    with TestClient(app, headers={"X-DMS-Principal": "ocr-service-tests"}) as client:
        response = client.get("/ocr-results/unbekannt:1/page-image")
    assert response.status_code == 404


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
