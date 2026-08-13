"""Zugriff auf die EIGENE (lokale) DMS-Installation - für Sperren/Kopieren
(Quellrolle) und für das tatsächliche Anlegen empfangener Ordner/Dokumente
(Zielrolle, siehe `main.py`s `/inbound/*`-Endpunkte). Beide Rollen greifen auf
dieselbe lokale `folder-service`/`document-service`-Instanz zu, nur die
Richtung unterscheidet sich.

Bewusst synchron (`httpx.Client`, `dms_connector_sdk.DmsTreeClient` ist es
ebenfalls, siehe dessen README) - migration-service ruft diese Funktionen aus
`async def`-FastAPI-Endpunkten heraus über `asyncio.to_thread()` auf, statt
selbst eine async-Variante zu bauen: `asyncio.to_thread()` (sync-Arbeit AUS
einem async-Kontext heraus in einen Thread auslagern) ist die unproblematische
Richtung, anders als das in P12-S1 bewusst vermiedene `asgiref.async_to_sync`
(async-Aufruf AUS synchronem Code heraus, verschachtelte Event-Loops)."""

import httpx
from dms_connector_sdk import DmsTreeClient
from migration_service.settings import Settings


class RoleAssignmentInfo:
    __slots__ = (
        "principal_type",
        "principal_id",
        "role_name",
        "role_description",
        "role_permissions",
    )

    def __init__(
        self,
        *,
        principal_type: str,
        principal_id: str,
        role_name: str,
        role_description: str,
        role_permissions: list[str],
    ) -> None:
        self.principal_type = principal_type
        self.principal_id = principal_id
        self.role_name = role_name
        self.role_description = role_description
        self.role_permissions = role_permissions


class LocalDmsClient:
    """Bündelt `DmsTreeClient` (Ordner-/Dokument-Baum) und einen dünnen
    `permission-service`-Client (Rollen/Zuweisungen) für die lokale
    Installation - ein Objekt statt zweier separater, da beide Rollen
    (Quelle liest, Ziel schreibt) beide Aspekte brauchen."""

    # Self-Gating (Post-Roadmap Phase 19 Session 6, ADR 0071) - `POST`/
    # `PUT /roles` und `POST`/`DELETE /scope-locks` verlangen seither
    # `admin.user_management`. migration-service weist sich diese Capability
    # zusätzlich zu `admin.object_config` an seinem eigenen Bootstrap zu
    # (siehe `main.py._ensure_config_admin_permission`).
    _PRINCIPAL_ID = "migration-service"

    def __init__(self, settings: Settings) -> None:
        self.tree = DmsTreeClient(
            document_service_base_url=settings.document_service_base_url,
            folder_service_base_url=settings.folder_service_base_url,
        )
        self._permissions = httpx.Client(
            base_url=settings.permission_service_base_url,
            timeout=30.0,
            headers={"X-DMS-Principal": self._PRINCIPAL_ID},
        )

    def close(self) -> None:
        self.tree.close()
        self._permissions.close()

    def acquire_scope_lock(self, resource_id: str, *, locked_by: str, reason: str) -> str:
        """Bereichssperre (4.7) auf den gesamten Quellordner-Unterbaum - erfüllt
        7.2s Schritt 1 ("Sperre des Quellordners inkl. aller Unterordner") durch
        Wiederverwendung des bereits bestehenden Mechanismus (P3-S4), statt ein
        eigenes Sperrsystem zu bauen. `blocks_read=false` (Default): 7.2 verlangt
        nur "keine Schreibzugriffe während der Migration", Lesen bleibt erlaubt."""
        response = self._permissions.post(
            "/scope-locks",
            json={"resource_id": resource_id, "locked_by": locked_by, "reason": reason},
        )
        response.raise_for_status()
        # `scope_lock.id` ist bei permission-service eine autoincrement-Integer-
        # PK, nicht ein UUID-String (real aufgetreten: asyncpg lehnte den
        # ungewandelten int beim Schreiben in die `String`-Spalte ab).
        return str(response.json()["scope_lock"]["id"])

    def release_scope_lock(self, scope_lock_id: str, *, released_by: str) -> None:
        response = self._permissions.request(
            "DELETE", f"/scope-locks/{scope_lock_id}", json={"released_by": released_by}
        )
        if response.status_code == 404:
            return
        response.raise_for_status()

    def list_role_assignments(self, resource_id: str) -> list[RoleAssignmentInfo]:
        """Rollenzuweisungen EINES Ordners (7.2 "Berechtigungen") - `role_name`/
        `description`/`permissions` statt der lokalen `role_id`: eine Ziel-
        Installation hat ihre eigene Rollennummerierung, der Name ist die
        einzige stabile, übertragbare Kennung (`Role.name` ist `unique`)."""
        assignments = self._permissions.get(
            "/role-assignments", params={"resource_id": resource_id}
        )
        assignments.raise_for_status()
        roles_response = self._permissions.get("/roles")
        roles_response.raise_for_status()
        roles_by_id = {r["id"]: r for r in roles_response.json()}
        result = []
        for assignment in assignments.json():
            role = roles_by_id.get(assignment["role_id"])
            if role is None:
                continue
            result.append(
                RoleAssignmentInfo(
                    principal_type=assignment["principal_type"],
                    principal_id=assignment["principal_id"],
                    role_name=role["name"],
                    role_description=role["description"],
                    role_permissions=role["permissions"],
                )
            )
        return result

    def apply_role_assignment(self, resource_id: str, assignment: RoleAssignmentInfo) -> None:
        """Get-or-Create der Rolle per Name, dann Zuweisung anlegen - beim
        Empfangen einer migrierten Berechtigung (Zielrolle). **Bewusste
        Grenze**: `principal_id` bleibt eine opake Referenz, keine Prüfung
        gegen die Nutzerpopulation dieser Installation (siehe
        docs/services/migration-service.md "Bewusste Grenzen")."""
        roles_response = self._permissions.get("/roles")
        roles_response.raise_for_status()
        existing = next(
            (r for r in roles_response.json() if r["name"] == assignment.role_name), None
        )
        if existing is not None:
            role_id = existing["id"]
        else:
            create_response = self._permissions.post(
                "/roles",
                json={
                    "name": assignment.role_name,
                    "description": assignment.role_description,
                    "permissions": assignment.role_permissions,
                },
            )
            create_response.raise_for_status()
            role_id = create_response.json()["id"]
        assignment_response = self._permissions.post(
            "/role-assignments",
            json={
                "principal_type": assignment.principal_type,
                "principal_id": assignment.principal_id,
                "role_id": role_id,
                "resource_id": resource_id,
            },
        )
        assignment_response.raise_for_status()
