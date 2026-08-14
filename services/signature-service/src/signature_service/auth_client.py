import httpx

from signature_service.connectors.interface import SignerInfo


class AuthServiceClient:
    """HTTP client against the Auth Service - `signer_principal_id` remains
    a self-reported field (consistent with `triggered_by`/`approved_by`/
    `completed_by`/`lifted_by` throughout the project), but is - same
    retrofit pattern as with notification-service (P6-S6) - checked against
    a real `auth-service` account and returns the display name/email for
    the AES certificate (3.10: "uniquely attributable to a person").
    `GET /users` has been gated since P6-S5 - logs in via the technical
    `users-admin` account, no token caching (see notification-service.
    auth_client for the same trade-off)."""

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

    async def resolve_signer(self, principal_id: str) -> SignerInfo | None:
        response = await self._client.get("/users", headers=await self._admin_headers())
        response.raise_for_status()
        for user in response.json():
            if user["username"] == principal_id:
                first = user.get("first_name") or ""
                last = user.get("last_name") or ""
                display_name = f"{first} {last}".strip() or principal_id
                return SignerInfo(
                    principal_id=principal_id,
                    display_name=display_name,
                    email=user.get("email") or "",
                )
        return None

    async def close(self) -> None:
        await self._client.aclose()
