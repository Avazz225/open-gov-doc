import httpx


class FolderClient:
    """HTTP client for the Folder Service (2.1) - Document Service uses
    this to check the existence of a given ``folder_id`` and read its
    ``object_type_id`` (2.2a, placement constraint), but does not keep its
    own copy of the folder structure."""

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
