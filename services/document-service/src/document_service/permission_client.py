import httpx


class PermissionServiceClient:
    """HTTP client for the Permission Service (4.1) - Document Service
    uses this to perform, for the first time, a genuine read-permission
    check itself (previously only connected via the `ApprovalClient` for
    the four-eyes principle, see docs/architecture.md "authorization ...
    still not enforced in several places"). Deliberately used only for the
    share link (4.2a, P14-S10) - a document that becomes reachable
    ANONYMOUSLY via a link justifies a genuine check at the point of
    creation, regardless of the fact that regular document access remains
    unchecked elsewhere in the system (the same, already documented gap)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def check_read(self, *, principal_id: str, resource_id: str) -> bool:
        response = await self._client.get(
            "/check",
            params={
                "principal_id": principal_id,
                "resource_id": resource_id,
                "permission": "document.read",
                "access_type": "read",
            },
        )
        response.raise_for_status()
        return bool(response.json()["allowed"])

    async def check_write(self, *, principal_id: str, resource_id: str) -> bool:
        """Direct Office editing (post-roadmap feature): a WebDAV edit
        token grants actual write permissions, not just read access like a
        share link - hence `access_type="write"` here instead of
        `check_read`'s `"read"`."""
        response = await self._client.get(
            "/check",
            params={
                "principal_id": principal_id,
                "resource_id": resource_id,
                "permission": "document.write",
                "access_type": "write",
            },
        )
        response.raise_for_status()
        return bool(response.json()["allowed"])

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        """Post-roadmap phase 19 session 10 (ADR 0075) - domain-admin
        capability check (`admin.legal_hold`), unlike `check_read`/
        `check_write` above this is not a resource-scoped RBAC check but a
        global role at the root resource."""
        response = await self._client.get(f"/effective-permissions/{principal_id}/root")
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def close(self) -> None:
        await self._client.aclose()
