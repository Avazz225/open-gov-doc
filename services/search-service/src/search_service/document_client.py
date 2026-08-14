from typing import Any

import httpx


class DocumentServiceClient:
    """HTTP client against the Document Service (3.1). Document events are
    deliberately thin (see consumer.py) - the full record is reloaded here
    on every event, instead of relying on event payload fields."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get(self, document_id: str) -> dict[str, Any] | None:
        response = await self._client.get(f"/documents/{document_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
