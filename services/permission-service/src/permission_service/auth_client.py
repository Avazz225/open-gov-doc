import httpx


class AuthServiceClient:
    """HTTP client against the auth service - the first cross-service
    dependency of this service in this direction (P6-S6, 4.8). `auth-service`
    has already been calling `permission-service` since P6-S5 (role
    assignment/check); this new reverse direction is not a runtime cycle,
    since both calls are independent, non-nested HTTP requests - just a
    denser dependency graph, see ADR 0024."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_active_superuser(self) -> tuple[bool, str | None]:
        """`(active, principal_id)` - needed to enforce `POST
        /maintenance-mode/lift` (4.8): only the currently active superuser
        may lift it."""
        response = await self._client.get("/superuser/status")
        response.raise_for_status()
        body = response.json()
        return body["active"], body.get("principal_id")

    async def close(self) -> None:
        await self._client.aclose()
