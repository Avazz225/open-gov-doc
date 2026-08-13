import asyncio
import os
import uuid
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from rendering_service.main import app

DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "rendering-service-tests"}) as c:
        yield c


def _real_png(color=(10, 200, 40)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (400, 300), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _upload_document(*, filename: str, content: bytes, content_type: str) -> str:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": filename, "created_by": "rendering-service-tests"},
        files={"file": (filename, content, content_type)},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["id"]


def _checkin_version(document_id: str, *, filename: str, content: bytes, content_type: str) -> None:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents/{document_id}/versions",
        data={"expected_base_version_number": 1, "created_by": "rendering-service-tests"},
        files={"file": (filename, content, content_type)},
        timeout=30.0,
    )
    response.raise_for_status()


async def _poll_until(predicate, timeout_seconds=10.0, interval=0.2) -> bool:
    elapsed = 0.0
    while elapsed < timeout_seconds:
        if predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


def test_document_created_event_triggers_thumbnail_rendering(client):
    document_id = _upload_document(
        filename=f"foto-{uuid.uuid4().hex[:8]}.png", content=_real_png(), content_type="image/png"
    )

    # Seit P7-S3 (5.6) matcht PdfArchiveRenderer zusaetzlich Rasterbilder -
    # ein .png erzeugt seither zwei Renditionen (thumbnail + pdf_archive),
    # daher auf beide warten statt auf die erste beliebige.
    def _ready() -> bool:
        body = client.get("/renditions", params={"document_id": document_id}).json()
        return len(body) >= 2 and all(r["status"] == "ready" for r in body)

    found = asyncio.run(_poll_until(_ready))
    assert found, "Rendering-Event wurde nicht rechtzeitig verarbeitet"

    renditions = client.get("/renditions", params={"document_id": document_id}).json()
    assert {r["rendition_type"] for r in renditions} == {"thumbnail", "pdf_archive"}
    assert all(r["version_number"] == 1 for r in renditions)


def test_document_version_created_event_triggers_rendering_for_new_version(client):
    document_id = _upload_document(
        filename=f"foto-{uuid.uuid4().hex[:8]}.png",
        content=_real_png(color=(1, 1, 1)),
        content_type="image/png",
    )

    def _v1_ready() -> bool:
        body = client.get(
            "/renditions", params={"document_id": document_id, "version_number": 1}
        ).json()
        return any(r["status"] == "ready" for r in body)

    assert asyncio.run(_poll_until(_v1_ready)), "Version 1 wurde nicht rechtzeitig gerendert"

    _checkin_version(
        document_id,
        filename=f"foto-v2-{uuid.uuid4().hex[:8]}.png",
        content=_real_png(color=(255, 0, 0)),
        content_type="image/png",
    )

    def _v2_ready() -> bool:
        body = client.get(
            "/renditions", params={"document_id": document_id, "version_number": 2}
        ).json()
        return any(r["status"] == "ready" for r in body)

    assert asyncio.run(_poll_until(_v2_ready)), "Version 2 wurde nicht rechtzeitig gerendert"

    all_renditions = client.get("/renditions", params={"document_id": document_id}).json()
    assert {r["version_number"] for r in all_renditions} == {1, 2}
