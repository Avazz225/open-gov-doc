from typing import Any

import httpx


class FolderServiceClient:
    """HTTP client against the Folder Service (3.1) - only for the
    denormalized `folder_name` in the search index (see consumer.py).
    Renaming a folder does not retroactively update already indexed
    documents, only on the next re-index (accepted inconsistency, see Open
    Points)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get(self, folder_id: str) -> dict[str, Any] | None:
        response = await self._client.get(f"/folders/{folder_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
