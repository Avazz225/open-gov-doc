import httpx


class PermissionServiceClient:
    """HTTP client against the Permission Service (P32-S3, ADR 0132) -
    department RBAC for departmental mailboxes, ADR 0123's own anticipated
    "Group-membership check layered onto the existing `poststelle_role`
    gate." The first group-membership-as-RBAC-gate pattern in this
    codebase - every existing `PermissionServiceClient` elsewhere only
    wraps capability/permission checks (`/check`, `/effective-permissions`),
    never group membership. No dedicated "is X a member of Y" endpoint
    exists at permission-service, so this fetches the member list (a
    department's membership is expected to stay small) and filters
    client-side - same "list, then filter" idiom document-service's own
    `has_permission` already uses for its own, differently-shaped check."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def is_group_member(self, group_id: str, principal_id: str) -> bool:
        response = await self._client.get(f"/groups/{group_id}/members")
        response.raise_for_status()
        return any(member["principal_id"] == principal_id for member in response.json())

    async def is_maintenance_active(self) -> bool:
        """Post-Roadmap Phase 44 Session 3 (ADR 0164) - same method
        `libs/dms-permission-client` gained this session, duplicated here
        for the same reason `document_service.permission_client` also
        keeps its own local copy rather than migrating onto the shared
        library."""
        response = await self._client.get("/maintenance-mode")
        response.raise_for_status()
        return bool(response.json()["active"])

    async def close(self) -> None:
        await self._client.aclose()
