import httpx

from signature_service.connectors.interface import SignerInfo


class AuthServiceClient:
    """HTTP-Client gegen den Auth Service - `signer_principal_id` bleibt ein
    selbstberichtetes Feld (konsistent mit `triggered_by`/`approved_by`/
    `completed_by`/`lifted_by` im gesamten Projekt), wird aber - gleiches
    Retrofit-Muster wie bei notification-service (P6-S6) - gegen ein echtes
    `auth-service`-Konto geprüft und liefert Anzeigename/E-Mail fürs
    AES-Zertifikat (3.10: "eindeutig einer Person zuordenbar"). `GET /users`
    ist seit P6-S5 gegated - Anmeldung über das technische `users-admin`-Konto,
    kein Token-Caching (siehe notification-service.auth_client für dieselbe
    Abwägung)."""

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
