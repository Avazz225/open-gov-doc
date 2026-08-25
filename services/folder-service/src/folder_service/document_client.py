import httpx


class DocumentClient:
    """HTTP client against the Document Service (5.2, since P7-S1b) -
    counterpart to `document_service.FolderClient` (P3-S3), just in the
    reverse direction. Cascades trash/restore of a folder subtree
    synchronously onto the documents contained within it, and checks before
    a forced folder deletion whether its subtree still contains active
    documents."""

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

    async def get(self, document_id: str) -> dict | None:
        """Hand folder reference resolution (14.2, post-roadmap phase 31
        session 7, ADR 0118) - exact mirror of case-service's own
        `DocumentClient.get()`. A soft-deleted document remains retrievable
        via `GET /documents/{id}` (no 404), which already covers "a
        reference survives the deletion of its original, traceably" without
        any extra logic here."""
        response = await self._client.get(f"/documents/{document_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
