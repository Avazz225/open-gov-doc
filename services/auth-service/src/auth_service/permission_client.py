import logging

import httpx
from dms_permission_client import PermissionServiceClient as _BasePermissionServiceClient
from dms_permission_client import RoleAssignmentPendingApprovalError, RoleNotFoundError

logger = logging.getLogger(__name__)

__all__ = [
    "PermissionServiceClient",
    "RoleAssignmentPendingApprovalError",
    "RoleNotFoundError",
]


class PermissionServiceClient(_BasePermissionServiceClient):
    """auth-service's local extension of the shared `dms-permission-client`
    base (P68-S2 migration) - keeps only the methods with no shared-lib
    equivalent: `requires_approval`/`request_approval` (Post-Roadmap Phase 39
    Session 3, ADR 0153 - cross-service four-eyes retrofit) and
    `revoke_all_role_assignments` (P55-S2 - user-deletion cleanup).
    `get_role_id`/`ensure_role_assignment`/`has_permission`/`close` now come
    from the shared base unchanged; `ROOT_RESOURCE_ID` likewise."""

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
