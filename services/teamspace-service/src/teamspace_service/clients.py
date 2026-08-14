"""Dünne HTTP-Clients gegen `folder-service`/`permission-service` (2.5, P14-S6)
- `teamspace-service` besitzt keinen eigenen Dokument-/Ordner-Speicher (analog
`case-service`s opaken `document_id`-Referenzen), sondern legt bei der Anlage
eines Teamspace einen echten `folder-service`-Ordner an und hält dessen `id`
als `root_folder_id` fest."""

import httpx


class FolderServiceClient:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def create_folder(self, *, name: str, created_by: str) -> dict:
        """Legt den Teamspace-Wurzelordner direkt unter dem globalen
        `folder-service`-Wurzelordner (`parent_id="root"`) an - bewusst OHNE
        `object_type_id` (Feld ist optional, `folder-service` überspringt die
        sonst live gegen `object-type-service` laufende Validierung dann
        vollständig, verifiziert): ein Teamspace-Ordner braucht keine eigenen
        Attribute/Constraints, ein dediziertes Objekttyp nur für diesen Zweck
        anzulegen wäre unnötige Komplexität."""
        response = await self._client.post(
            "/folders", json={"name": name, "parent_id": "root", "created_by": created_by}
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class PermissionServiceClient:
    """Verknüpft eine Teamspace-Mitgliedschaft zusätzlich mit einer echten,
    ressourcen-skopierten `permission-service`-Rollenzuweisung auf dem
    Teamspace-Wurzelordner (P14-S6) - NICHT die primäre Zugriffskontrolle
    dieses Service (das ist die eigene `teamspace_member`-Tabelle, siehe
    `models.py`/`main.py._require_member`), sondern eine zusätzliche,
    forward-kompatible Verankerung: `search-service` prüft bereits heute real
    `document.read` auf Ordnerebene (`POST /check/batch`) - Suchergebnisse aus
    einem Teamspace respektieren die Mitgliedschaft dadurch schon jetzt, ohne
    dass dieser Service `search-service` kennen muss. Andere, noch nicht
    RBAC-durchsetzende Services (`folder-service`/`document-service` selbst,
    siehe `docs/architecture.md`) profitieren erst von einer künftigen
    Durchsetzung dort."""

    TEAMSPACE_MEMBER_ROLE_NAME = "teamspace-member"
    TEAMSPACE_MEMBER_ROLE_PERMISSIONS = [
        "document.read",
        "document.write",
        "folder.read",
        "folder.write",
    ]

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)
        self._role_id: int | None = None

    async def _ensure_role(self) -> int:
        """Get-or-Create der Rolle per Name (gleiches Muster wie
        `migration-service`s `apply_role_assignment` für migrierte
        Berechtigungen) - `POST /roles`/`POST /role-assignments` sind bei
        `permission-service` bewusst ungegatet (verifiziert), kein
        technisches Konto/Principal für diesen Bootstrap-Aufruf nötig."""
        if self._role_id is not None:
            return self._role_id
        response = await self._client.get("/roles")
        response.raise_for_status()
        existing = next(
            (r for r in response.json() if r["name"] == self.TEAMSPACE_MEMBER_ROLE_NAME), None
        )
        if existing is not None:
            self._role_id = existing["id"]
        else:
            create_response = await self._client.post(
                "/roles",
                json={
                    "name": self.TEAMSPACE_MEMBER_ROLE_NAME,
                    "description": "Teamspace-Mitgliedschaft (2.5) - automatisch verwaltet, "
                    "nicht von Hand zuzuweisen",
                    "permissions": self.TEAMSPACE_MEMBER_ROLE_PERMISSIONS,
                },
            )
            create_response.raise_for_status()
            self._role_id = create_response.json()["id"]
        return self._role_id

    async def grant_resource_access(self, *, principal_id: str, resource_id: str) -> None:
        role_id = await self._ensure_role()
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

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        """Post-Roadmap Phase 22 Session 5 - Domain-Admin-Capability-Prüfung
        (`admin.teamspace_management`) für die neue installationsweite
        Teamspace-Übersicht, gleiches Muster wie `document_service.
        permission_client.PermissionServiceClient.has_permission`: globale
        Rolle an der Wurzelressource, keine ressourcenskalierte Prüfung."""
        response = await self._client.get(f"/effective-permissions/{principal_id}/root")
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def revoke_resource_access(self, *, principal_id: str, resource_id: str) -> None:
        role_id = await self._ensure_role()
        response = await self._client.get(
            "/role-assignments", params={"principal_id": principal_id, "resource_id": resource_id}
        )
        response.raise_for_status()
        for assignment in response.json():
            if assignment["role_id"] == role_id:
                delete_response = await self._client.delete(f"/role-assignments/{assignment['id']}")
                delete_response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
