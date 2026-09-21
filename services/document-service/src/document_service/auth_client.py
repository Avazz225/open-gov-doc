import httpx


class AuthServiceClient:
    """HTTP client against `auth-service` (P66-S1) - currently only for
    checking that a role name referenced in `AuditTraceRoleOverride`
    actually exists as a Keycloak realm role, closing a gap this codebase
    already accepted for every other free-text role-name field
    (`docs/services/document-service.md`'s own "the same existing gap as
    for every other role name in the system")."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def realm_role_exists(self, role: str) -> bool:
        response = await self._client.get("/realm-roles")
        response.raise_for_status()
        return any(entry["name"] == role for entry in response.json())

    async def close(self) -> None:
        await self._client.aclose()
