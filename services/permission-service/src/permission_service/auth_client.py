import httpx


class AuthServiceClient:
    """HTTP client against the auth service - the first cross-service
    dependency of this service in this direction (P6-S6, 4.8). `auth-service`
    has already been calling `permission-service` since P6-S5 (role
    assignment/check); this new reverse direction is not a runtime cycle,
    since both calls are independent, non-nested HTTP requests - just a
    denser dependency graph, see ADR 0024."""

    def __init__(self, base_url: str) -> None:
        # Phase 79 Session 3 (ADR 0200's own documented structural conflict):
        # this client's ONLY caller, `main._is_active_superuser`, is a
        # best-effort bypass check invoked on the hot path of EVERY
        # `_require_role_management` call (`POST`/`PUT /roles`, `POST
        # /role-assignments`, `PUT /approval-config/*`) - already fails safe
        # on any exception, but a hung (not just refused) connection to an
        # unreachable auth-service (a stopped container mid-test, or a real
        # rolling restart in production) previously meant every one of those
        # writes could silently wait up to 30s before falling back. A short,
        # dedicated timeout bounds that worst case without changing the
        # success path (a healthy round trip is normally well under 100ms).
        self._client = httpx.AsyncClient(base_url=base_url, timeout=3.0)

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
