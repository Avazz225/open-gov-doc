from typing import Literal

import httpx


class RoleNotFoundError(Exception):
    pass


class RoleAssignmentPendingApprovalError(Exception):
    pass


class PermissionServiceClient:
    """HTTP client against the Permission Service - consolidates the
    `PermissionServiceClient` class previously duplicated per service
    (Post-Roadmap Phase 19 Session 1) into a shared package. Covers the four
    operations shared across the service duplicates (`check`, `check_batch`,
    `has_permission`, `ensure_role_assignment`) - service-specific extra
    methods (e.g. `workflow-service`'s `check_delegation`, `query-service`'s
    four-eyes principle endpoints, `teamspace-service`'s role bootstrap)
    deliberately remain in their respective services; this is not a
    refactor-for-its-own-sake of the existing duplicates. New consumers from
    this session onward use this package directly."""

    ROOT_RESOURCE_ID = "root"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def check(
        self,
        *,
        principal_id: str,
        resource_id: str,
        permission: str,
        access_type: Literal["read", "write"] = "read",
    ) -> bool:
        """Single check against `GET /check` - generalizes the
        `check_read`/`check_write` pair previously duplicated in
        `document-service` via the `access_type` parameter."""
        response = await self._client.get(
            "/check",
            params={
                "principal_id": principal_id,
                "resource_id": resource_id,
                "permission": permission,
                "access_type": access_type,
            },
        )
        response.raise_for_status()
        return bool(response.json()["allowed"])

    async def check_batch(
        self,
        *,
        principal_id: str,
        permission: str,
        access_type: Literal["read", "write"] = "read",
        resource_ids: list[str],
    ) -> dict[str, bool]:
        if not resource_ids:
            return {}
        response = await self._client.post(
            "/check/batch",
            json={
                "principal_id": principal_id,
                "permission": permission,
                "access_type": access_type,
                "resource_ids": resource_ids,
            },
        )
        response.raise_for_status()
        return response.json()["results"]

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        response = await self._client.get(
            f"/effective-permissions/{principal_id}/{self.ROOT_RESOURCE_ID}"
        )
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def get_role_id(self, name: str) -> int | None:
        response = await self._client.get("/roles")
        response.raise_for_status()
        for role in response.json():
            if role["name"] == name:
                return role["id"]
        return None

    async def ensure_role_assignment(
        self, *, principal_id: str, role_name: str, resource_id: str = ROOT_RESOURCE_ID
    ) -> None:
        """Idempotent: assigns `principal_id` the role identified by name on
        the given resource (default: root resource), unless that assignment
        already exists."""
        role_id = await self.get_role_id(role_name)
        if role_id is None:
            raise RoleNotFoundError(f"Rolle {role_name!r} ist im Permission Service unbekannt")

        existing = await self._client.get(
            "/role-assignments", params={"principal_id": principal_id}
        )
        existing.raise_for_status()
        for assignment in existing.json():
            same_role = assignment["role_id"] == role_id
            same_resource = assignment["resource_id"] == resource_id
            if same_role and same_resource:
                return

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
        # `POST /role-assignments` always returns 2xx, even when
        # `permission.role_assignment.create` requires the four-eyes
        # principle on this installation (ADR 0060) - in that case the
        # assignment itself does NOT yet exist, only an open approval
        # request. Without this check the method would falsely report
        # success.
        if response.json()["status"] != "created":
            raise RoleAssignmentPendingApprovalError(
                f"Rollenzuweisung für {principal_id!r}/{role_name!r} wartet auf Genehmigung "
                "(permission.role_assignment.create ist auf dieser Installation Vier-Augen-"
                "pflichtig) - noch nicht wirksam"
            )

    async def close(self) -> None:
        await self._client.aclose()
