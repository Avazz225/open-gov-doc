"""Access to the OWN (local) DMS installation - for locking/copying (source
role) and for actually creating received folders/documents (target role, see
`main.py`'s `/inbound/*` endpoints). Both roles access the same local
`folder-service`/`document-service` instance, only the direction differs.

Deliberately synchronous (`httpx.Client`, `dms_connector_sdk.DmsTreeClient`
is as well, see its README) - migration-service calls these functions from
`async def` FastAPI endpoints via `asyncio.to_thread()`, instead of building
its own async variant: `asyncio.to_thread()` (offloading sync work FROM an
async context into a thread) is the unproblematic direction, unlike
`asgiref.async_to_sync` (async call FROM synchronous code, nested event
loops), which was deliberately avoided in P12-S1."""

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
    """Bundles `DmsTreeClient` (folder/document tree) and a thin
    `permission-service` client (roles/assignments) for the local
    installation - one object instead of two separate ones, since both
    roles (source reads, target writes) need both aspects."""

    # Self-gating (Post-Roadmap Phase 19 Session 6, ADR 0071) - since then
    # `POST`/`PUT /roles` and `POST`/`DELETE /scope-locks` require
    # `admin.user_management`. migration-service assigns itself this
    # capability in addition to `admin.object_config` during its own
    # bootstrap (see `main.py._ensure_config_admin_permission`).
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
        """Scope lock (4.7) on the entire source folder subtree - fulfills
        7.2's step 1 ("locking of the source folder including all
        subfolders") by reusing the already-existing mechanism (P3-S4),
        instead of building a dedicated locking system. `blocks_read=false`
        (default): 7.2 only requires "no write access during migration",
        reading remains allowed."""
        response = self._permissions.post(
            "/scope-locks",
            json={"resource_id": resource_id, "locked_by": locked_by, "reason": reason},
        )
        response.raise_for_status()
        # `scope_lock.id` is an autoincrement integer PK in permission-service,
        # not a UUID string (encountered in practice: asyncpg rejected the
        # unconverted int when writing to the `String` column).
        return str(response.json()["scope_lock"]["id"])

    def release_scope_lock(self, scope_lock_id: str, *, released_by: str) -> None:
        response = self._permissions.request(
            "DELETE", f"/scope-locks/{scope_lock_id}", json={"released_by": released_by}
        )
        if response.status_code == 404:
            return
        response.raise_for_status()

    def list_role_assignments(self, resource_id: str) -> list[RoleAssignmentInfo]:
        """Role assignments of ONE folder (7.2 "permissions") - `role_name`/
        `description`/`permissions` instead of the local `role_id`: a
        target installation has its own role numbering, the name is the
        only stable, transferable identifier (`Role.name` is `unique`)."""
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
        """Get-or-create the role by name, then create the assignment - used
        when receiving a migrated permission (target role). **Deliberate
        limitation**: `principal_id` remains an opaque reference, no
        validation against this installation's user population (see
        docs/services/migration-service.md "Deliberate Limitations")."""
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
