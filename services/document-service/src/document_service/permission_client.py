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

    async def check_read(
        self, *, principal_id: str, resource_id: str, permission: str = "document.read"
    ) -> bool:
        """`permission` (Post-Roadmap Phase 38 Session 4, ADR 0149):
        defaults to the new baseline `document.read` (now granted to
        "everyone", see `permission-service`'s `EVERYONE_ROLE_PERMISSIONS`)
        - every call site below that predates this session and relied on
        `document.read` being a narrow, admin-granted permission (share
        links, redaction, export) now passes its OWN dedicated permission
        string instead, so "everyone" gaining baseline read access doesn't
        silently defeat those pre-existing, deliberately narrower gates."""
        response = await self._client.get(
            "/check",
            params={
                "principal_id": principal_id,
                "resource_id": resource_id,
                "permission": permission,
                "access_type": "read",
            },
        )
        response.raise_for_status()
        return bool(response.json()["allowed"])

    async def check_write(
        self, *, principal_id: str, resource_id: str, permission: str = "document.write"
    ) -> bool:
        """Direct Office editing (post-roadmap feature): a WebDAV edit
        token grants actual write permissions, not just read access like a
        share link - hence `access_type="write"` here instead of
        `check_read`'s `"read"`. `permission` - see `check_read`'s
        docstring for why this is no longer hardcoded."""
        response = await self._client.get(
            "/check",
            params={
                "principal_id": principal_id,
                "resource_id": resource_id,
                "permission": permission,
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

    async def create_resource_node(
        self, *, resource_id: str, parent_id: str | None, resource_type: str = "folder"
    ) -> None:
        """`POST /resources` (Post-Roadmap Phase 39 Session 4, ADR 0154) -
        synchronous, idempotent create-if-missing for a `ResourceNode`,
        giving each document its own real per-document RBAC resource
        (mirrors `case-service`'s identical use of this endpoint since ADR
        0144). A purely event-driven registration (`document.resource.
        created`, published alongside this call for symmetry/self-healing)
        would leave a race window where a freshly created document is
        briefly unreadable by anyone, since an unregistered `resource_id`
        denies every check outright rather than falling back to root - see
        `dms_permission_client.PermissionServiceClient.create_resource_node`
        (the shared library `case-service`/`folder-service` use) for the
        identical rationale in full. Deliberately duplicated here rather
        than migrating `document-service` onto the shared library outright
        - a separate, independent piece of tech debt not attempted in this
        session (see ADR 0154 "Consequences")."""
        response = await self._client.post(
            "/resources",
            json={
                "resource_id": resource_id,
                "parent_id": parent_id,
                "resource_type": resource_type,
            },
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
