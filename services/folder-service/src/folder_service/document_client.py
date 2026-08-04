import httpx


class DocumentClient:
    """HTTP-Client gegen den Document Service (5.2, seit P7-S1b) - Gegenstück
    zu `document_service.FolderClient` (P3-S3), nur in umgekehrter Richtung.
    Kaskadiert Papierkorb/Wiederherstellung eines Ordner-Teilbaums synchron
    auf die darin enthaltenen Dokumente und prüft vor einer Ordner-
    Zwangslöschung, ob dessen Teilbaum noch aktive Dokumente enthält."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def cascade_trash(
        self, folder_ids: list[str], *, via_folder_id: str, deleted_by: str
    ) -> list[str]:
        response = await self._client.post(
            "/documents/cascade-trash",
            json={
                "folder_ids": folder_ids,
                "via_folder_id": via_folder_id,
                "deleted_by": deleted_by,
            },
        )
        response.raise_for_status()
        return response.json()["document_ids"]

    async def cascade_restore(self, via_folder_id: str) -> list[str]:
        response = await self._client.post(
            "/documents/cascade-restore", json={"via_folder_id": via_folder_id}
        )
        response.raise_for_status()
        return response.json()["document_ids"]

    async def count_active(self, folder_ids: list[str]) -> int:
        response = await self._client.post(
            "/documents/count-active", json={"folder_ids": folder_ids}
        )
        response.raise_for_status()
        return response.json()["count"]

    async def close(self) -> None:
        await self._client.aclose()
