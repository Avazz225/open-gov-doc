from typing import Any

import httpx


class FolderServiceClient:
    """HTTP-Client gegen den Folder Service (3.1) - nur für die denormalisierte
    `folder_name` im Suchindex (siehe consumer.py). Ein Ordner-Umbenennen
    aktualisiert bereits indexierte Dokumente nicht rückwirkend, erst beim
    nächsten Re-Index (akzeptierte Inkonsistenz, siehe Offene Punkte)."""

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
