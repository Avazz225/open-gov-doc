import httpx


class ObjectNotFoundError(Exception):
    """The Storage Service does not (or no longer) know the requested object
    key."""


class StorageClient:
    """Thin HTTP client against the Storage Service API (3.6), identical in
    scope to the equally named client of the Document Service - the virus
    scan service also never holds file contents itself."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def upload(self, key: str, data: bytes, content_type: str | None) -> None:
        headers = {"Content-Type": content_type} if content_type else {}
        response = await self._client.put(f"/objects/{key}", content=data, headers=headers)
        response.raise_for_status()

    async def download(self, key: str) -> bytes:
        """Reads back a quarantined object's content (2.5, P15-S2) - e.g. to
        pass it on to the Document Service upon release."""
        response = await self._client.get(f"/objects/{key}")
        if response.status_code == 404:
            raise ObjectNotFoundError(key)
        response.raise_for_status()
        return response.content

    async def delete(self, key: str) -> None:
        """Permanently removes the quarantined object content (2.5, P15-S2).
        Idempotent - an already missing object is not an error, since a
        release/deletion never needs the same bytes twice."""
        response = await self._client.delete(f"/objects/{key}")
        if response.status_code == 404:
            return
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
