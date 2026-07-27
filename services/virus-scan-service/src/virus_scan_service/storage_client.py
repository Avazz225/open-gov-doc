import httpx


class StorageClient:
    """Dünner HTTP-Client gegen die Storage-Service-API (3.6), identisch im
    Zuschnitt zum gleichnamigen Client des Document Service - auch der Virus-
    Scan Service hält nie selbst Dateiinhalte."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def upload(self, key: str, data: bytes, content_type: str | None) -> None:
        headers = {"Content-Type": content_type} if content_type else {}
        response = await self._client.put(f"/objects/{key}", content=data, headers=headers)
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
