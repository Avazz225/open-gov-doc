import httpx


class AuthServiceClient:
    """HTTP-Client gegen den Auth Service - erste Cross-Service-Abhängigkeit
    dieses Service in dieser Richtung (P6-S6, 4.8). `auth-service` ruft
    bereits seit P6-S5 `permission-service` auf (Rollenzuweisung/-Check);
    diese neue Gegenrichtung ist kein Laufzeit-Zirkel, da beide Aufrufe
    unabhängige, nicht verschachtelte HTTP-Requests sind - nur eine dichtere
    Abhängigkeitsgrafik, siehe ADR 0024."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_active_superuser(self) -> tuple[bool, str | None]:
        """`(active, principal_id)` - nötig, um `POST /maintenance-mode/lift`
        (4.8) durchzusetzen: nur der gerade aktive Superuser darf aufheben."""
        response = await self._client.get("/superuser/status")
        response.raise_for_status()
        body = response.json()
        return body["active"], body.get("principal_id")

    async def close(self) -> None:
        await self._client.aclose()
