from typing import Literal

import httpx


class PermissionServiceClient:
    """HTTP-Client gegen den Permission Service (3.1) - nutzt den neuen
    `POST /check/batch` (P5-S4). Suchergebnisse werden über ihre `folder_id`
    geprüft, nicht über die `document_id` selbst: Dokumente sind keine eigenen
    Permission-Resources, nur Ordner werden als `ResourceNode` geführt (siehe
    docs/services/search-service.md, Abschnitt Berechtigungsfilterung)."""

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
