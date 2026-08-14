import httpx


class AuthServiceClient:
    """HTTP client against the Auth Service - retrofit P6-S6 (call authorization):
    `POST /notifications` checks for `channel in {"email","in_app"}` whether
    the given recipient is a real account, instead of accepting it blindly.
    Affects exclusively this public endpoint - the internal SLA/
    break-glass/emergency-shutdown alerting path in `consumer.py` calls
    `repository.create_and_send` directly, without going through this client.

    `GET /users` has itself been gated since P6-S5 - this client logs in
    with the technical `users-admin` account for that purpose. No token caching (the
    call is rare enough that a fresh login per check is not worth the
    expiration-time complexity)."""

    def __init__(self, base_url: str, *, admin_username: str, admin_password: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)
        self._admin_username = admin_username
        self._admin_password = admin_password

    async def _admin_headers(self) -> dict[str, str]:
        response = await self._client.post(
            "/login", json={"username": self._admin_username, "password": self._admin_password}
        )
        response.raise_for_status()
        token = response.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    async def recipient_exists(self, recipient: str, *, channel: str) -> bool:
        if channel == "webhook":
            return True  # Target is a URL, not an identity - nothing to check.
        response = await self._client.get("/users", headers=await self._admin_headers())
        response.raise_for_status()
        users = response.json()
        if channel == "email":
            return any(u.get("email") == recipient for u in users)
        return any(u["username"] == recipient for u in users)

    async def close(self) -> None:
        await self._client.aclose()
