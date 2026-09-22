from dms_permission_client import PermissionServiceClient as _BasePermissionServiceClient


class PermissionServiceClient(_BasePermissionServiceClient):
    """mail-connector's local extension of the shared `dms-permission-client`
    base (P68-S2 migration) - keeps only `is_group_member` (P32-S3, ADR
    0132), which has no shared-lib equivalent: the first
    group-membership-as-RBAC-gate pattern in this codebase - every other
    `PermissionServiceClient` consumer only wraps capability/permission
    checks (`/check`, `/effective-permissions`), never group membership. No
    dedicated "is X a member of Y" endpoint exists at permission-service, so
    this fetches the member list (a department's membership is expected to
    stay small) and filters client-side - same "list, then filter" idiom
    document-service's own `has_permission` already uses for its own,
    differently-shaped check. `is_maintenance_active`/`close` now come from
    the shared base unchanged."""

    async def is_group_member(self, group_id: str, principal_id: str) -> bool:
        response = await self._client.get(f"/groups/{group_id}/members")
        response.raise_for_status()
        return any(member["principal_id"] == principal_id for member in response.json())
