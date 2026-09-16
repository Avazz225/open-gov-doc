import httpx


class FolderClient:
    """HTTP client for the Folder Service (2.1) - Document Service uses
    this to check the existence of a given ``folder_id`` and read its
    ``object_type_id`` (2.2a, placement constraint), but does not keep its
    own copy of the folder structure."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get(self, folder_id: str, *, x_dms_principal: str = "") -> dict | None:
        """`x_dms_principal` (Post-Roadmap Phase 38 Session 4, ADR 0149):
        `GET /folders/{id}` now requires a valid principal - forwarding the
        acting user's own header (rather than a fixed service identity) is
        deliberate, since this call is also the parent-folder existence/
        placement check for document creation and moves: a fixed service
        identity would not be a teamspace member and would incorrectly
        401/403 legitimate moves into a teamspace folder, whereas the real
        caller's principal resolves exactly like every other permission
        check in this session."""
        response = await self._client.get(
            f"/folders/{folder_id}", headers={"X-DMS-Principal": x_dms_principal}
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
