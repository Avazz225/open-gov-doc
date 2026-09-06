from datetime import datetime
from typing import Literal

import httpx


class PermissionServiceClient:
    """HTTP client against the Permission Service - Retrofit P6-S6:
    (a) process definitions (BPMN/script task upload) require the domain
    admin capability `admin.object_config` (same pattern as `auth-service`'s
    `_require_user_management`, P6-S5); (b) the SLA poll loop respects the
    system-wide emergency lock (4.8). Deliberately still its own local
    copy instead of `libs/dms-permission-client` (P19-S1) - `check_delegation`
    below is a service-specific extra method that, per ADR 0066, is
    deliberately not moved into the shared package; a full migration
    would add no value here."""

    ROOT_RESOURCE_ID = "root"

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        response = await self._client.get(
            f"/effective-permissions/{principal_id}/{self.ROOT_RESOURCE_ID}"
        )
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def check(
        self,
        *,
        principal_id: str,
        resource_id: str,
        permission: str,
        access_type: Literal["read", "write"] = "read",
    ) -> bool:
        """Post-Roadmap Phase 19 Session 9 (ADR 0074) - single check against
        `GET /check` (including scope-lock overlay), unlike `has_permission`
        above (a plain permission list without lock evaluation).
        Same signature as `libs/dms-permission-client`'s `check`."""
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

    async def is_maintenance_active(self) -> bool:
        response = await self._client.get("/maintenance-mode")
        response.raise_for_status()
        return response.json()["active"]

    async def check_delegation(
        self,
        *,
        deputy_principal_id: str,
        delegator_principal_id: str,
        process_definition_id: int,
        object_type_id: int | None = None,
        folder_resource_id: str | None = None,
    ) -> bool:
        """Deputizing during absence (4.4a, P14-S11) - true if
        ``deputy_principal_id`` is currently registered as an active
        deputy for ``delegator_principal_id`` (time window + optional
        process/object-type/folder scope), see main.py's ``complete_task``.
        ``object_type_id``/``folder_resource_id`` activate the previously
        dead scope dimensions since P32-S2 (ADR 0048's own anticipated
        "additional resolution step") - see main.py's
        ``_resolve_business_key_scope``, which resolves them from the
        instance's ``business_key`` before this call."""
        params: dict[str, str | int] = {
            "deputy_principal_id": deputy_principal_id,
            "delegator_principal_id": delegator_principal_id,
            "process_definition_id": process_definition_id,
        }
        if object_type_id is not None:
            params["object_type_id"] = object_type_id
        if folder_resource_id is not None:
            params["folder_resource_id"] = folder_resource_id
        response = await self._client.get("/delegations/check", params=params)
        response.raise_for_status()
        return bool(response.json()["allowed"])

    async def create_org_hierarchy_grant(
        self,
        *,
        principal_id: str,
        grant_kind: str,
        process_definition_id: int,
        ends_at: datetime,
    ) -> dict:
        """Dynamic org-hierarchy-based temporary access grant (14.2,
        Post-Roadmap Phase 31 Session 10) - resolves ``principal_id``'s
        supervisor(s)/chain/org-unit at permission-service and auto-creates
        one ``Delegation`` per resolved deputy. Returns the raw response
        dict (``{"delegation_ids": [...], "deputy_principal_ids": [...]}``)
        - callers need both: the delegation IDs to store for later
        revocation (``main.py``'s ``TaskClaim.granted_delegation_ids``) and
        the deputy IDs to report back to whoever requested the grant."""
        response = await self._client.post(
            "/org-hierarchy-grants",
            json={
                "principal_id": principal_id,
                "grant_kind": grant_kind,
                "process_definition_id": process_definition_id,
                "ends_at": ends_at.isoformat(),
            },
        )
        response.raise_for_status()
        return response.json()

    async def revoke_org_hierarchy_grant(self, delegation_id: str) -> None:
        """Cleanup counterpart (Post-Roadmap Phase 31 Session 10) - ends an
        auto-created delegation early instead of waiting for its backstop
        `ends_at`, called when a claim is released or its task completes.
        Tolerant of an already-revoked/unknown ID (`404`) - best-effort
        cleanup must not fail the primary action (claim release/task
        completion) that triggered it."""
        response = await self._client.delete(f"/org-hierarchy-grants/{delegation_id}")
        if response.status_code == 404:
            return
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
