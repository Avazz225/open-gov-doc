import httpx


class OcrServiceClient:
    """HTTP-Client gegen den OCR Service (3.9, P5-S3) - bevorzugte
    Volltextquelle für den Suchindex (siehe consumer.py: OCR zuerst, sonst
    substitute_text-Rendition als Fallback)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_full_text(self, document_id: str, version_number: int) -> str | None:
        response = await self._client.get(
            "/ocr-results", params={"document_id": document_id, "version_number": version_number}
        )
        response.raise_for_status()
        results = response.json()
        for result in results:
            if result["status"] in ("ready", "needs_review") and result["full_text"].strip():
                return result["full_text"]
        return None

    async def close(self) -> None:
        await self._client.aclose()
