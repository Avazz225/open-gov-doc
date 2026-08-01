import httpx


class AuthServiceClient:
    """HTTP-Client gegen den Auth Service - Retrofit P6-S6 (Aufrufautorisierung):
    `POST /notifications` prüft für `channel in {"email","in_app"}`, ob der
    angegebene Empfänger ein echtes Konto ist, statt ihn blind zu übernehmen.
    Betrifft ausschließlich diesen öffentlichen Endpunkt - der interne SLA-/
    Break-Glass-/Not-Shutdown-Alarmierungspfad in `consumer.py` ruft
    `repository.create_and_send` direkt auf, ohne über diesen Client zu gehen.

    `GET /users` ist seit P6-S5 selbst gegated - dieser Client meldet sich
    dafür mit dem technischen `users-admin`-Konto an. Kein Token-Caching (der
    Aufruf ist selten genug, dass eine frische Anmeldung je Prüfung die
    Ablaufzeit-Komplexität nicht wert ist)."""

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
            return True  # Ziel ist eine URL, keine Identität - nichts zu prüfen.
        response = await self._client.get("/users", headers=await self._admin_headers())
        response.raise_for_status()
        users = response.json()
        if channel == "email":
            return any(u.get("email") == recipient for u in users)
        return any(u["username"] == recipient for u in users)

    async def close(self) -> None:
        await self._client.aclose()
