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
        self, *, deputy_principal_id: str, delegator_principal_id: str, process_definition_id: int
    ) -> bool:
        """Deputizing during absence (4.4a, P14-S11) - true if
        ``deputy_principal_id`` is currently registered as an active
        deputy for ``delegator_principal_id`` (time window + optional
        process scope), see main.py's ``complete_task``."""
        response = await self._client.get(
            "/delegations/check",
            params={
                "deputy_principal_id": deputy_principal_id,
                "delegator_principal_id": delegator_principal_id,
                "process_definition_id": process_definition_id,
            },
        )
        response.raise_for_status()
        return bool(response.json()["allowed"])

    async def close(self) -> None:
        await self._client.aclose()
