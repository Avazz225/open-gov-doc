import httpx


class FolderClient:
    """HTTP-Client gegen den Folder Service (2.1) - Document Service prüft
    darüber die Existenz eines angegebenen ``folder_id`` und liest dessen
    ``object_type_id`` (2.2a, Platzierungs-Constraint), besitzt aber keine
    eigene Kopie der Ordnerstruktur."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get(self, folder_id: str) -> dict | None:
        response = await self._client.get(f"/folders/{folder_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
