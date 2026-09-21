import httpx


class ObjectNotFoundError(Exception):
    pass


class StorageClient:
    """Thin HTTP client against the storage-service API (3.6), identical
    shape as in every other service in this project - `mail-connector`
    never holds file content itself."""

    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "mail-connector"}

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
        if response.status_code == 404:
            raise ObjectNotFoundError(key)
        response.raise_for_status()
        return response.content

    async def delete(self, key: str) -> None:
        response = await self._client.delete(f"/objects/{key}")
        if response.status_code == 404:
            return
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
