import httpx


class PermissionServiceClient:
    """HTTP-Client gegen den Permission Service - erste Cross-Service-
    Abhängigkeit von `auth-service` überhaupt (P6-S5). Nutzt ausschließlich
    bereits bestehende, ungegatete Endpunkte (`/roles`, `/role-assignments`,
    `/effective-permissions/{principal_id}/{resource_id}`) - kein neuer
    permission-service-Endpunkt nötig."""

    ROOT_RESOURCE_ID = "root"

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get_role_id(self, name: str) -> int | None:
        response = await self._client.get("/roles")
        response.raise_for_status()
        for role in response.json():
            if role["name"] == name:
                return role["id"]
        return None

    async def ensure_role_assignment(self, *, principal_id: str, role_name: str) -> None:
        """Idempotent (P6-S5): weist `principal_id` die per Name benannte,
        systemeigene Domain-Admin-Rolle (4.6) an der Wurzelressource zu,
        sofern das noch nicht der Fall ist."""
        role_id = await self.get_role_id(role_name)
        if role_id is None:
            raise RoleNotFoundError(f"Rolle {role_name!r} ist im Permission Service unbekannt")

        existing = await self._client.get(
            "/role-assignments", params={"principal_id": principal_id}
        )
        existing.raise_for_status()
        for assignment in existing.json():
            same_role = assignment["role_id"] == role_id
            same_resource = assignment["resource_id"] == self.ROOT_RESOURCE_ID
            if same_role and same_resource:
                return

        response = await self._client.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": principal_id,
                "role_id": role_id,
                "resource_id": self.ROOT_RESOURCE_ID,
            },
        )
        response.raise_for_status()

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        response = await self._client.get(
            f"/effective-permissions/{principal_id}/{self.ROOT_RESOURCE_ID}"
        )
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def close(self) -> None:
        await self._client.aclose()


class RoleNotFoundError(Exception):
    pass
