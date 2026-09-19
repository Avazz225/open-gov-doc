import httpx

_SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "notification-service"}


class AuthServiceClient:
    """HTTP client against the Auth Service - retrofit P6-S6 (call authorization):
    `POST /notifications` checks for `channel in {"email","in_app"}` whether
    the given recipient is a real account, instead of accepting it blindly.
    Affects exclusively this public endpoint - the internal SLA/
    break-glass/emergency-shutdown alerting path in `consumer.py` calls
    `repository.create_and_send` directly, without going through this client.

    **Phase 50 Session 2**: previously logged in as the `users-admin`
    technical account (full domain-admin - user CRUD + AD-group->role
    mapping control) just for this read-only lookup, a real excess-privilege
    exposure. Now asserts a fixed service identity (`X-DMS-Principal:
    notification-service`, same pattern `signature-service` already used
    against `document-service`) against the new, narrow
    `GET /users/service-directory` (capability `service.user_lookup`, ADR-
    free hardening - see `docs/services/auth-service.md`). No token/header
    caching needed any more either, since there's no login round-trip."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def recipient_exists(self, recipient: str, *, channel: str) -> bool:
        if channel == "webhook":
            return True  # Target is a URL, not an identity - nothing to check.
        response = await self._client.get(
            "/users/service-directory", headers=_SYSTEM_PRINCIPAL_HEADERS
        )
        response.raise_for_status()
        users = response.json()
        if channel == "email":
            return any(u.get("email") == recipient for u in users)
        return any(u["username"] == recipient for u in users)

    async def close(self) -> None:
        await self._client.aclose()
