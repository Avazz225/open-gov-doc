import httpx


class StorageClient:
    """Thin client for storage-service's object API - ETSI validation reports
    of incoming-signature runs (P92-S2). `signature-service` is a trusted
    caller there (ADR 0179)."""

    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "signature-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers=self._SYSTEM_PRINCIPAL_HEADERS
        )

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        response = await self._client.put(
            f"/objects/{key}", content=data, headers={"Content-Type": content_type}
        )
        response.raise_for_status()

    async def download(self, key: str) -> bytes:
        response = await self._client.get(f"/objects/{key}")
        response.raise_for_status()
        return response.content

    async def close(self) -> None:
        await self._client.aclose()
