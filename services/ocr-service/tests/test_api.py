from fastapi.testclient import TestClient
from ocr_service.main import app


def test_healthz():
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "ocr-service"


def test_list_ocr_results_empty_for_unknown_document():
    with TestClient(app) as client:
        response = client.get("/ocr-results", params={"document_id": "unbekannt"})
    assert response.status_code == 200
    assert response.json() == []


def test_get_ocr_result_404():
    with TestClient(app) as client:
        response = client.get("/ocr-results/unbekannt:1")
    assert response.status_code == 404


def test_download_page_image_404_for_unknown_result():
    with TestClient(app) as client:
        response = client.get("/ocr-results/unbekannt:1/page-image")
    assert response.status_code == 404
