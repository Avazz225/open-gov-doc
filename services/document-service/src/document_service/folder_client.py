import httpx


class FolderClient:
    """HTTP-Client gegen den Folder Service (2.1) - Document Service prüft
    darüber nur die Existenz eines angegebenen ``folder_id``, besitzt aber
    keine eigene Kopie der Ordnerstruktur."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def exists(self, folder_id: str) -> bool:
        response = await self._client.get(f"/folders/{folder_id}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    async def close(self) -> None:
        await self._client.aclose()
