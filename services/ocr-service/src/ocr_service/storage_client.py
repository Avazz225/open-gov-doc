import httpx


class StorageClient:
    """Thin HTTP client against the storage service (3.6) - standalone OCR
    page images (PDFs) are stored there permanently, not in the OCR service
    itself or in a transient cache."""

    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "ocr-service"}

    def __init__(self, base_url: str) -> None:
        """Phase 59 Session 2 (ADR 0179): storage-service's object-CRUD
        endpoints now require a known trusted-caller identity."""
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers=self._SYSTEM_PRINCIPAL_HEADERS
        )

    async def upload(self, key: str, data: bytes, content_type: str | None) -> None:
        headers = {"Content-Type": content_type} if content_type else {}
        response = await self._client.put(f"/objects/{key}", content=data, headers=headers)
        response.raise_for_status()

    async def download(self, key: str) -> bytes:
        response = await self._client.get(f"/objects/{key}")
        response.raise_for_status()
        return response.content

    async def close(self) -> None:
        await self._client.aclose()
