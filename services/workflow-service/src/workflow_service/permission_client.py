import httpx


class PermissionServiceClient:
    """HTTP-Client gegen den Permission Service - Retrofit P6-S6:
    (a) Prozessdefinitionen (BPMN-/Script-Task-Upload) verlangen die Domain-
    Admin-Capability `admin.object_config` (gleiches Muster wie `auth-service`s
    `_require_user_management`, P6-S5); (b) der SLA-Poll-Loop respektiert die
    systemweite Notfallsperre (4.8)."""

    ROOT_RESOURCE_ID = "root"

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        response = await self._client.get(
            f"/effective-permissions/{principal_id}/{self.ROOT_RESOURCE_ID}"
        )
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def is_maintenance_active(self) -> bool:
        response = await self._client.get("/maintenance-mode")
        response.raise_for_status()
        return response.json()["active"]

    async def check_delegation(
        self, *, deputy_principal_id: str, delegator_principal_id: str, process_definition_id: int
    ) -> bool:
        """Stellvertretung bei Abwesenheit (4.4a, P14-S11) - wahr, wenn
        ``deputy_principal_id`` gerade aktiv als Stellvertretung fuer
        ``delegator_principal_id`` eingetragen ist (Zeitfenster + optionaler
        Prozess-Scope), siehe main.py's ``complete_task``."""
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
