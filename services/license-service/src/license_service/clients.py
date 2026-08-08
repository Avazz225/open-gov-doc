import httpx


class StorageClient:
    """Duenner HTTP-Client gegen storage-service - `GET /storage/usage`
    direkt statt eines Umwegs ueber reporting-service (Service-Isolation,
    storage-service bleibt Quelle der Wahrheit fuer seinen eigenen
    Speicherverbrauch, siehe P9-S0-Recherche)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def total_bytes(self) -> int:
        response = await self._client.get("/storage/usage")
        response.raise_for_status()
        return sum(entry["total_size_bytes"] for entry in response.json())

    async def close(self) -> None:
        await self._client.aclose()


class DocumentClient:
    """HTTP-Client gegen document-service - `GET /documents/count-active-total`
    (neu, seit P9-S1) fuer die installationsweite Dokumentenzahl-Dimension."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def count_active_total(self) -> int:
        response = await self._client.get("/documents/count-active-total")
        response.raise_for_status()
        return response.json()["count"]

    async def close(self) -> None:
        await self._client.aclose()


class AuthServiceClient:
    """HTTP-Client gegen auth-service - Superuser-Status (4.6, kein Header-
    Shortcut, 1:1 Muster aus query-service) sowie die beiden neuen, seit
    P9-S1 ungegateten internen Endpunkte fuer die Nutzeranzahl-Dimension
    (9.1: wahlweise gleichzeitige Sessions oder benannte Accounts)."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_active_superuser(self) -> tuple[bool, str | None]:
        response = await self._client.get("/superuser/status")
        response.raise_for_status()
        body = response.json()
        return body["active"], body.get("principal_id")

    async def concurrent_session_count(self) -> int:
        response = await self._client.get("/sessions/count")
        response.raise_for_status()
        return response.json()["count"]

    async def named_user_count(self) -> int:
        response = await self._client.get("/users/count")
        response.raise_for_status()
        return response.json()["count"]

    async def close(self) -> None:
        await self._client.aclose()


class PermissionServiceClient:
    """HTTP-Client gegen permission-service - aktiviert erstmals die seit
    Langem vorgeseedete, aber unbenutzte Domain-Admin-Rolle `domain-admin-
    license` (`admin.license`), 1:1 Gate-Muster aus query-service."""

    ROOT_RESOURCE_ID = "root"

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        response = await self._client.get(
            f"/effective-permissions/{principal_id}/{self.ROOT_RESOURCE_ID}"
        )
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def close(self) -> None:
        await self._client.aclose()
