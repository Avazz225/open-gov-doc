from typing import Literal

import httpx


class PermissionServiceClient:
    """HTTP client against the Permission Service (3.1) - uses the new
    `POST /check/batch` (P5-S4). Search results are checked via their
    `folder_id`, not via the `document_id` itself: documents are not their
    own permission resources, only folders are maintained as `ResourceNode`
    (see docs/services/search-service.md, permission filtering section)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def check_batch(
        self,
        *,
        principal_id: str,
        permission: str,
        access_type: Literal["read", "write"] = "read",
        resource_ids: list[str],
    ) -> dict[str, bool]:
        if not resource_ids:
            return {}
        response = await self._client.post(
            "/check/batch",
            json={
                "principal_id": principal_id,
                "permission": permission,
                "access_type": access_type,
                "resource_ids": resource_ids,
            },
        )
        response.raise_for_status()
        return response.json()["results"]

    async def close(self) -> None:
        await self._client.aclose()
