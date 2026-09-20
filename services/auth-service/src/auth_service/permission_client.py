import logging

import httpx

logger = logging.getLogger(__name__)


class PermissionServiceClient:
    """HTTP client against the Permission Service - `auth-service`'s first
    ever cross-service dependency (P6-S5). Uses exclusively already-existing,
    ungated endpoints (`/roles`, `/role-assignments`,
    `/effective-permissions/{principal_id}/{resource_id}`) - no new
    permission-service endpoint needed."""

    ROOT_RESOURCE_ID = "root"

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_role_id(self, name: str) -> int | None:
        response = await self._client.get("/roles")
        response.raise_for_status()
        for role in response.json():
            if role["name"] == name:
                return role["id"]
        return None

    async def ensure_role_assignment(self, *, principal_id: str, role_name: str) -> None:
        """Idempotent (P6-S5): assigns `principal_id` the native domain
        admin role (4.6) named by name at the root resource, provided this
        is not already the case."""
        role_id = await self.get_role_id(role_name)
        if role_id is None:
            raise RoleNotFoundError(f"Rolle {role_name!r} ist im Permission Service unbekannt")

        existing = await self._client.get(
            "/role-assignments", params={"principal_id": principal_id}
        )
        existing.raise_for_status()
        for assignment in existing.json():
            same_role = assignment["role_id"] == role_id
            same_resource = assignment["resource_id"] == self.ROOT_RESOURCE_ID
            if same_role and same_resource:
                return

        response = await self._client.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": principal_id,
                "role_id": role_id,
                "resource_id": self.ROOT_RESOURCE_ID,
            },
        )
        response.raise_for_status()
        # Since P17-S3, `POST /role-assignments` always returns 2xx, even
        # when `permission.role_assignment.create` requires the four-eyes
        # principle on this installation ("permission change", ADR 0060) -
        # the assignment itself does NOT yet exist then, only an open
        # approval request. Without this check, the method would falsely
        # report success - the caller in `main.py`'s lifespan already
        # catches `Exception` and logs a retry on the next restart, exactly
        # the right behavior for this case.
        if response.json()["status"] != "created":
            raise RoleAssignmentPendingApprovalError(
                f"Rollenzuweisung für {principal_id!r}/{role_name!r} wartet auf Genehmigung "
                "(permission.role_assignment.create ist auf dieser Installation Vier-Augen-"
                "pflichtig) - noch nicht wirksam"
            )

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        response = await self._client.get(
            f"/effective-permissions/{principal_id}/{self.ROOT_RESOURCE_ID}"
        )
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def requires_approval(self, action_type: str) -> bool:
        """Post-Roadmap Phase 39 Session 3 (ADR 0153) - `auth-service`'s
        first REMOTE check of `permission-service`'s approval-config, as
        opposed to `permission-service`'s own gated endpoints (e.g. `POST
        /roles`, ADR 0130/0151) which read `ApprovalActionConfig` directly
        from their own database. Cross-service four-eyes retrofits before
        this session (`auth.superuser.activate`, ADR 0023/0024) never had
        this check at all - there is no direct/bypass endpoint for
        superuser activation, so approval is unconditionally mandatory for
        it. AD-group-mapping changes are meaningfully less sensitive, so
        this mirrors the OPTIONAL, per-action-type-configurable pattern
        used everywhere else in this project instead."""
        response = await self._client.get(f"/approval-config/{action_type}")
        response.raise_for_status()
        return bool(response.json()["requires_approval"])

    async def request_approval(self, *, action_type: str, initiated_by: str, payload: dict) -> str:
        """Creates a pending approval request on `permission-service` via
        its existing generic `POST /approval-requests` endpoint (unlike
        `permission-service`'s own gated endpoints, which call their
        internal `_request_approval` directly) - returns the new request's
        id."""
        response = await self._client.post(
            "/approval-requests",
            json={"action_type": action_type, "initiated_by": initiated_by, "payload": payload},
        )
        response.raise_for_status()
        return str(response.json()["id"])

    async def revoke_all_role_assignments(self, principal_id: str) -> None:
        """P55-S2: called from `DELETE /users/{id}` - `permission-service`'s
        own `delete_group` deliberately documents leaving `RoleAssignment`
        rows behind as harmless once a group no longer matches any
        principal, but a deleted USER has no equivalent existing
        precedent/justification here, and the stale rows are real
        data-hygiene debt (e.g. breaking `GET /users/lookup` resolution for
        admin/UI display of a reference to an account that can never
        authenticate again). Idempotent - a principal with no assignments
        is a silent no-op. Fail-soft (logs and returns): by the time this
        runs, the Keycloak account is already gone - the security-relevant
        part of the deletion already succeeded, this is best-effort hygiene
        cleanup, not itself worth failing the whole `DELETE /users/{id}`
        call over if `permission-service` happens to be unreachable."""
        try:
            response = await self._client.get(
                "/role-assignments", params={"principal_id": principal_id}
            )
            response.raise_for_status()
            for assignment in response.json():
                delete_response = await self._client.delete(f"/role-assignments/{assignment['id']}")
                delete_response.raise_for_status()
        except httpx.HTTPError:
            logger.warning(
                "role_assignment_cleanup_failed: principal_id=%s", principal_id, exc_info=True
            )

    async def close(self) -> None:
        await self._client.aclose()


class RoleNotFoundError(Exception):
    pass


class RoleAssignmentPendingApprovalError(Exception):
    pass
