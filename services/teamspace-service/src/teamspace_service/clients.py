"""Thin HTTP clients against `folder-service`/`permission-service` (2.5,
P14-S6) - `teamspace-service` has no document/folder storage of its own
(analogous to `case-service`'s opaque `document_id` references), but
instead creates a real `folder-service` folder when a teamspace is
created and keeps its `id` as `root_folder_id`."""

import httpx


class FolderServiceClient:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def create_folder(self, *, name: str, created_by: str) -> dict:
        """Creates the teamspace root folder directly under the global
        `folder-service` root folder (`parent_id="root"`) - deliberately
        WITHOUT `object_type_id` (the field is optional; `folder-service`
        then skips entirely the validation that otherwise runs live
        against `object-type-service`, verified): a teamspace folder
        needs no attributes/constraints of its own, and creating a
        dedicated object type just for this purpose would be unnecessary
        complexity."""
        response = await self._client.post(
            "/folders", json={"name": name, "parent_id": "root", "created_by": created_by}
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class PermissionServiceClient:
    """Additionally links a teamspace membership with a real, resource-
    scoped `permission-service` role assignment on the teamspace root
    folder (P14-S6) - NOT the primary access control of this service
    (that is the service's own `teamspace_member` table, see
    `models.py`/`main.py._require_member`), but an additional, forward-
    compatible anchoring: `search-service` already really checks
    `document.read` at folder level today (`POST /check/batch`) - search
    results from a teamspace therefore already respect membership now,
    without this service needing to know about `search-service`. Other
    services that don't yet enforce RBAC (`folder-service`/
    `document-service` themselves, see `docs/architecture.md`) will only
    benefit once enforcement is added there in the future."""

    TEAMSPACE_MEMBER_ROLE_NAME = "teamspace-member"
    TEAMSPACE_MEMBER_ROLE_PERMISSIONS = [
        "document.read",
        "document.write",
        "folder.read",
        "folder.write",
    ]

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)
        self._role_id: int | None = None

    async def _ensure_role(self) -> int:
        """Get-or-create the role by name (same pattern as
        `migration-service`'s `apply_role_assignment` for migrated
        permissions) - `POST /roles`/`POST /role-assignments` are
        deliberately ungated on `permission-service` (verified), no
        technical account/principal needed for this bootstrap call."""
        if self._role_id is not None:
            return self._role_id
        response = await self._client.get("/roles")
        response.raise_for_status()
        existing = next(
            (r for r in response.json() if r["name"] == self.TEAMSPACE_MEMBER_ROLE_NAME), None
        )
        if existing is not None:
            self._role_id = existing["id"]
        else:
            create_response = await self._client.post(
                "/roles",
                json={
                    "name": self.TEAMSPACE_MEMBER_ROLE_NAME,
                    "description": "Teamspace-Mitgliedschaft (2.5) - automatisch verwaltet, "
                    "nicht von Hand zuzuweisen",
                    "permissions": self.TEAMSPACE_MEMBER_ROLE_PERMISSIONS,
                },
            )
            create_response.raise_for_status()
            self._role_id = create_response.json()["id"]
        return self._role_id

    async def grant_resource_access(self, *, principal_id: str, resource_id: str) -> None:
        role_id = await self._ensure_role()
        response = await self._client.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": principal_id,
                "role_id": role_id,
                "resource_id": resource_id,
            },
        )
        response.raise_for_status()

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        """Post-Roadmap Phase 22 Session 5 - domain-admin capability check
        (`admin.teamspace_management`) for the new installation-wide
        teamspace overview, same pattern as `document_service.
        permission_client.PermissionServiceClient.has_permission`: global
        role on the root resource, no resource-scoped check."""
        response = await self._client.get(f"/effective-permissions/{principal_id}/root")
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def revoke_resource_access(self, *, principal_id: str, resource_id: str) -> None:
        role_id = await self._ensure_role()
        response = await self._client.get(
            "/role-assignments", params={"principal_id": principal_id, "resource_id": resource_id}
        )
        response.raise_for_status()
        for assignment in response.json():
            if assignment["role_id"] == role_id:
                delete_response = await self._client.delete(f"/role-assignments/{assignment['id']}")
                delete_response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
