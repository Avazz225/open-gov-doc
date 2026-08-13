from typing import Literal

import httpx


class RoleNotFoundError(Exception):
    pass


class RoleAssignmentPendingApprovalError(Exception):
    pass


class PermissionServiceClient:
    """HTTP-Client gegen den Permission Service - konsolidiert die zuvor je
    Service duplizierte `PermissionServiceClient`-Klasse (Post-Roadmap Phase
    19 Session 1) in ein gemeinsames Paket. Deckt die vier über die
    Serviceduplikate hinweg gemeinsamen Operationen ab (`check`, `check_batch`,
    `has_permission`, `ensure_role_assignment`) - service-spezifische
    Zusatzmethoden (z. B. `workflow-service`s `check_delegation`,
    `query-service`s Vier-Augen-Endpunkte, `teamspace-service`s
    Rollen-Bootstrap) bleiben bewusst in den jeweiligen Services, kein
    Selbstzweck-Refactor der bestehenden Duplikate. Neue Konsumenten ab
    dieser Session nutzen direkt dieses Paket."""

    ROOT_RESOURCE_ID = "root"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def check(
        self,
        *,
        principal_id: str,
        resource_id: str,
        permission: str,
        access_type: Literal["read", "write"] = "read",
    ) -> bool:
        """Einzelprüfung gegen `GET /check` - verallgemeinert das zuvor in
        `document-service` duplizierte `check_read`/`check_write`-Paar über
        den `access_type`-Parameter."""
        response = await self._client.get(
            "/check",
            params={
                "principal_id": principal_id,
                "resource_id": resource_id,
                "permission": permission,
                "access_type": access_type,
            },
        )
        response.raise_for_status()
        return bool(response.json()["allowed"])

    async def check_batch(
        self,
        *,
        principal_id: str,
        permission: str,
        access_type: Literal["read", "write"] = "read",
        resource_ids: list[str],
    ) -> dict[str, bool]:
        if not resource_ids:
            return {}
        response = await self._client.post(
            "/check/batch",
            json={
                "principal_id": principal_id,
                "permission": permission,
                "access_type": access_type,
                "resource_ids": resource_ids,
            },
        )
        response.raise_for_status()
        return response.json()["results"]

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        response = await self._client.get(
            f"/effective-permissions/{principal_id}/{self.ROOT_RESOURCE_ID}"
        )
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def get_role_id(self, name: str) -> int | None:
        response = await self._client.get("/roles")
        response.raise_for_status()
        for role in response.json():
            if role["name"] == name:
                return role["id"]
        return None

    async def ensure_role_assignment(
        self, *, principal_id: str, role_name: str, resource_id: str = ROOT_RESOURCE_ID
    ) -> None:
        """Idempotent: weist `principal_id` die per Name benannte Rolle am
        angegebenen Resource (Default: Wurzelressource) zu, sofern das noch
        nicht der Fall ist."""
        role_id = await self.get_role_id(role_name)
        if role_id is None:
            raise RoleNotFoundError(f"Rolle {role_name!r} ist im Permission Service unbekannt")

        existing = await self._client.get(
            "/role-assignments", params={"principal_id": principal_id}
        )
        existing.raise_for_status()
        for assignment in existing.json():
            same_role = assignment["role_id"] == role_id
            same_resource = assignment["resource_id"] == resource_id
            if same_role and same_resource:
                return

        response = await self._client.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": principal_id,
                "role_id": role_id,
                "resource_id": resource_id,
            },
        )
        response.raise_for_status()
        # `POST /role-assignments` liefert immer 2xx, auch wenn
        # `permission.role_assignment.create` auf dieser Installation
        # Vier-Augen-pflichtig ist (ADR 0060) - die Zuweisung selbst existiert
        # dann noch NICHT, nur ein offener Genehmigungsantrag. Ohne diese
        # Prüfung würde die Methode fälschlich Erfolg melden.
        if response.json()["status"] != "created":
            raise RoleAssignmentPendingApprovalError(
                f"Rollenzuweisung für {principal_id!r}/{role_name!r} wartet auf Genehmigung "
                "(permission.role_assignment.create ist auf dieser Installation Vier-Augen-"
                "pflichtig) - noch nicht wirksam"
            )

    async def close(self) -> None:
        await self._client.aclose()
