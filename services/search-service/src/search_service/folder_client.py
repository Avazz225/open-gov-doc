from typing import Any

import httpx


class FolderServiceClient:
    """HTTP client against the Folder Service (3.1) - only for the
    denormalized `folder_name` in the search index (see consumer.py).
    Renaming a folder does not retroactively update already indexed
    documents, only on the next re-index (accepted inconsistency, see Open
    Points)."""

    # Post-Roadmap Phase 38 Session 4: folder-service's primary endpoints now
    # require a non-empty `X-DMS-Principal` header - asserts a fixed service
    # identity (background/internal caller, no real end-user context
    # available; covered by "everyone"'s grants, same reasoning as
    # `archival_service.clients.DocumentClient`), same identity
    # `document_client.DocumentServiceClient` in this service already uses.
    _SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "search-service"}

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers=self._SYSTEM_PRINCIPAL_HEADERS
        )

    async def get(self, folder_id: str) -> dict[str, Any] | None:
        response = await self._client.get(f"/folders/{folder_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
