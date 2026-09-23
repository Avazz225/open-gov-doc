import httpx


class AuthServiceClient:
    """HTTP client against auth-service (Post-Roadmap Phase 74 Session 2,
    ADR 0195/ADR 0211's own named, deliberately-deferred-until-now gap:
    this service had no local `_is_active_superuser` helper at all, unlike
    permission-service/query-service/plugin-orchestration-service). `GET
    /superuser/status` is the only way to check "is the current caller the
    activated superuser" (4.6, no header shortcut) - same 1:1 pattern as
    those other services' own `AuthServiceClient`."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_active_superuser(self) -> tuple[bool, str | None]:
        response = await self._client.get("/superuser/status")
        response.raise_for_status()
        body = response.json()
        return body["active"], body.get("principal_id")

    async def close(self) -> None:
        await self._client.aclose()
