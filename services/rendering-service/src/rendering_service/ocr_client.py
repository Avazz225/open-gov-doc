import httpx


class OcrServiceClient:
    """Thin HTTP client against the OCR Service (3.1) - for the follow-up
    effect from P5-S3 (2.4/3.9: the OCR full text feeds a `substitute_text`
    rendition for documents that P5-S2 could not serve for lack of OCR).
    `ocr.completed` events deliberately carry only status fields, not the
    potentially large full text itself (see consumer.py) - this client
    fetches it via HTTP on demand."""

    # RBAC (post-roadmap phase 19 session 8, ADR 0073) - `GET /ocr-results/
    # {id}` has required `ocr.read` ever since; this call runs from within a
    # NATS consumer, no human principal available - same `system:<Service>`
    # pattern as `archival-service`'s `CaseClient`.
    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "system:rendering-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers=self._SYSTEM_PRINCIPAL_HEADERS
        )

    async def get_full_text(self, document_id: str, version_number: int) -> str | None:
        response = await self._client.get(f"/ocr-results/{document_id}:{version_number}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["full_text"]

    async def close(self) -> None:
        await self._client.aclose()
