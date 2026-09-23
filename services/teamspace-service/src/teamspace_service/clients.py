"""Thin HTTP clients against `folder-service`/`permission-service` (2.5,
P14-S6) - `teamspace-service` has no document/folder storage of its own
(analogous to `case-service`'s opaque `document_id` references), but
instead creates a real `folder-service` folder when a teamspace is
created and keeps its `id` as `root_folder_id`."""

import httpx


class FolderServiceClient:
    _PRINCIPAL_ID = "teamspace-service"

    def __init__(self, base_url: str) -> None:
        """`X-DMS-Principal` (Post-Roadmap Phase 38 Session 4, ADR 0149):
        `POST /folders` now requires a valid principal - same fixed
        service-identity pattern as this module's `PermissionServiceClient`
        (which has needed one since P32-S1)."""
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers={"X-DMS-Principal": self._PRINCIPAL_ID}
        )

    async def create_folder(self, *, name: str, created_by: str) -> dict:
        """Creates the teamspace root folder directly under the global
        `folder-service` root folder (`parent_id="root"`) - deliberately
        WITHOUT `object_type_id` (the field is optional; `folder-service`
        then skips entirely the validation that otherwise runs live
        against `object-type-service`, verified): a teamspace folder
        needs no attributes/constraints of its own, and creating a
        dedicated object type just for this purpose would be unnecessary
        complexity."""
        response = await self._client.post(
            "/folders", json={"name": name, "parent_id": "root", "created_by": created_by}
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class AuthServiceClient:
    """Group-member lookup against `auth-service` (2.5, Post-Roadmap Phase
    74 Session 3, ADR 0160/ADR 0217) - the specific missing piece a
    teamspace AD-group invitation needs. Same fixed service-identity
    pattern as `FolderServiceClient`/`PermissionServiceClient` above
    (`X-DMS-Principal: teamspace-service`), gated on `auth-service`'s side
    by the new, narrow `service.group_lookup` capability (seeded role
    `service-group-lookup` in `permission-service`) - an operator must
    grant this role to the `teamspace-service` principal once, same
    manual-grant convention already established for `service-user-lookup`
    (`notification-service`/`signature-service`, Phase 50 Session 2)."""

    _PRINCIPAL_ID = "teamspace-service"

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=10.0, headers={"X-DMS-Principal": self._PRINCIPAL_ID}
        )

    async def get_group_members(self, name: str) -> list[dict] | None:
        """`None` if no group with this exact name exists - the caller
        (`main.py`) turns that into a `404` for the interactive preview/
        bind endpoints, or simply skips that binding for a tick of the
        reconciliation poll loop (a group deleted out from under an
        existing binding is not itself an error condition worth crashing
        the loop over, see `_ad_group_reconciliation_poll_loop`)."""
        response = await self._client.get(f"/groups/{name}/members")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


class PermissionServiceClient:
    """Additionally links a teamspace membership with a real, resource-
    scoped `permission-service` role assignment on the teamspace root
    folder (P14-S6) - NOT the primary access control of this service
    (that is the service's own `teamspace_member` table, see
    `models.py`/`main.py._require_member`), but an additional, forward-
    compatible anchoring: `search-service` already really checks
    `document.read` at folder level today (`POST /check/batch`) - search
    results from a teamspace therefore already respect membership now,
    without this service needing to know about `search-service`. Other
    services that don't yet enforce RBAC (`folder-service`/
    `document-service` themselves, see `docs/architecture.md`) will only
    benefit once enforcement is added there in the future."""

    TEAMSPACE_MEMBER_ROLE_NAME = "teamspace-member"
    TEAMSPACE_MEMBER_ROLE_PERMISSIONS = [
        "document.read",
        "document.write",
        "folder.read",
        "folder.write",
    ]
    # Post-Roadmap Phase 44 Session 2 (ADR 0163): a SECOND role, granted
    # IN ADDITION to `teamspace-member` for members with
    # `can_manage_members=True` only. Before this, every member got
    # `folder.write` on the teamspace root folder regardless of manager
    # status - since `folder-service`'s `DELETE /folders/{id}`/
    # `POST /folders/{id}/trash` now check the narrower `folder.delete`
    # instead (see `docs/services/folder-service.md`), a non-manager
    # member without this second role can no longer delete/trash the
    # entire teamspace by calling folder-service directly, closing the
    # bypass around this service's own manager-only `DELETE /teamspaces/
    # {id}` guard.
    TEAMSPACE_MANAGER_ROLE_NAME = "teamspace-manager"
    TEAMSPACE_MANAGER_ROLE_PERMISSIONS = ["folder.delete"]
    _PRINCIPAL_ID = "teamspace-service"

    def __init__(self, base_url: str) -> None:
        """Since P32-S1 (ADR 0130): `POST /roles` is now self-gated
        (`admin.user_management`) - this client attaches
        `X-DMS-Principal` to every call, same pattern as `config-
        service`'s/`migration-service`'s `PermissionServiceClient`. The
        `teamspace-service` principal is granted `domain-admin-users` at
        startup, see `main.py._ensure_bootstrap_permissions`."""
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=30.0, headers={"X-DMS-Principal": self._PRINCIPAL_ID}
        )
        self._role_ids: dict[str, int] = {}

    async def _ensure_role(self, name: str, permissions: list[str], description: str) -> int:
        """Get-or-create the role by name (same pattern as
        `migration-service`'s `apply_role_assignment` for migrated
        permissions). `POST /roles` now wraps its response in a
        `status`/`role` envelope (P32-S1, ADR 0130) - unwrap `["role"]`
        instead of reading fields directly off the top-level object.
        Generalized from a single hard-coded role (Phase 44 Session 2) to
        also cover `TEAMSPACE_MANAGER_ROLE_NAME` - cached per role name,
        not just once, since this client now manages two."""
        if name in self._role_ids:
            return self._role_ids[name]
        response = await self._client.get("/roles")
        response.raise_for_status()
        existing = next((r for r in response.json() if r["name"] == name), None)
        if existing is not None:
            role_id = existing["id"]
        else:
            create_response = await self._client.post(
                "/roles",
                json={"name": name, "description": description, "permissions": permissions},
            )
            create_response.raise_for_status()
            role_id = create_response.json()["role"]["id"]
        self._role_ids[name] = role_id
        return role_id

    async def _ensure_member_role(self) -> int:
        return await self._ensure_role(
            self.TEAMSPACE_MEMBER_ROLE_NAME,
            self.TEAMSPACE_MEMBER_ROLE_PERMISSIONS,
            "Teamspace-Mitgliedschaft (2.5) - automatisch verwaltet, nicht von Hand zuzuweisen",
        )

    async def _ensure_manager_role(self) -> int:
        return await self._ensure_role(
            self.TEAMSPACE_MANAGER_ROLE_NAME,
            self.TEAMSPACE_MANAGER_ROLE_PERMISSIONS,
            "Teamspace-Verwaltung (2.5, ADR 0163) - zusätzlich zu 'teamspace-member' für "
            "Mitglieder mit can_manage_members=true, automatisch verwaltet, nicht von Hand "
            "zuzuweisen",
        )

    async def ensure_isolated_resource(self, *, resource_id: str, parent_id: str) -> None:
        """Post-Roadmap Phase 38 Session 4 (ADR 0149) - the resource-tree
        anchoring below (`grant_resource_access`) has existed since ADR
        0043, but `folder-service`/`document-service` themselves never
        actually enforced any RBAC check until this session; now that
        they do, a teamspace's root folder must stop the ancestor walk
        BEFORE it reaches the true `root` node (where `document.read`/
        `.write`/`folder.read`/`.write` are granted to "everyone" to
        preserve default openness for every OTHER, non-teamspace folder)
        - otherwise "everyone"'s grant at `root` would propagate straight
        into the teamspace and defeat the whole point. `POST /resources`
        is the synchronous, idempotent create-if-missing counterpart to
        the async `"folder.resource.created"` event (ADR 0144) - calling
        it here closes a pre-existing race (this method used to rely on
        that event having already landed by the time `grant_resource_
        access` below runs its own lookup) as a side effect. `PATCH
        /resources/{id}` then sets `inherit=False` - both endpoints are
        deliberately ungated (see permission-service's own docstrings),
        same trust boundary as the event bus."""
        create_response = await self._client.post(
            "/resources",
            json={"resource_id": resource_id, "parent_id": parent_id, "resource_type": "folder"},
        )
        create_response.raise_for_status()
        patch_response = await self._client.patch(
            f"/resources/{resource_id}", json={"inherit": False}
        )
        patch_response.raise_for_status()

    async def restore_default_inheritance(self, *, resource_id: str) -> None:
        """Post-Roadmap Phase 38 Session 4 (ADR 0149) - counterpart to
        `ensure_isolated_resource` above, called from `delete_teamspace`.
        `delete_teamspace` deliberately keeps the root folder itself
        ("deletes only the teamspace metadata... the root folder remains",
        see its own docstring - a pre-existing decision, not revisited
        here) - but without this, that kept folder stays `inherit=False`
        forever with every member's grant just revoked, i.e. a folder
        NOBODY can ever reach again, not even its creator, with no UI
        trace pointing back to it. Resetting `inherit` here makes the kept
        folder behave like any other ordinary, non-teamspace folder under
        `root` again (open via "everyone", like before it ever became a
        teamspace) instead of a permanently sealed dead end."""
        response = await self._client.patch(f"/resources/{resource_id}", json={"inherit": True})
        response.raise_for_status()

    async def _grant(self, *, role_id: int, principal_id: str, resource_id: str) -> None:
        """Idempotent (Phase 44 Session 2) - checks for an existing
        assignment first, same pattern as `dms_permission_client.
        PermissionServiceClient.ensure_role_assignment`. Needed now that
        `grant_manager_access` can be called from `update_member` on an
        ALREADY-manager (e.g. re-saving the same `can_manage_members=true`)
        - `permission-service`'s `POST /role-assignments` has no unique
        constraint of its own and would otherwise accumulate duplicate
        rows on every repeated call."""
        existing = await self._client.get(
            "/role-assignments", params={"principal_id": principal_id, "resource_id": resource_id}
        )
        existing.raise_for_status()
        if any(a["role_id"] == role_id for a in existing.json()):
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

    async def grant_resource_access(self, *, principal_id: str, resource_id: str) -> None:
        role_id = await self._ensure_member_role()
        await self._grant(role_id=role_id, principal_id=principal_id, resource_id=resource_id)

    async def grant_manager_access(self, *, principal_id: str, resource_id: str) -> None:
        """Phase 44 Session 2 (ADR 0163) - grants the second,
        `folder.delete`-carrying role. Called alongside `grant_resource_
        access` (never instead of it - a manager still needs the base
        member role too) whenever a member has/gains `can_manage_
        members=true`."""
        role_id = await self._ensure_manager_role()
        await self._grant(role_id=role_id, principal_id=principal_id, resource_id=resource_id)

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        """Post-Roadmap Phase 22 Session 5 - domain-admin capability check
        (`admin.teamspace_management`) for the new installation-wide
        teamspace overview, same pattern as `document_service.
        permission_client.PermissionServiceClient.has_permission`: global
        role on the root resource, no resource-scoped check."""
        response = await self._client.get(f"/effective-permissions/{principal_id}/root")
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def _revoke(self, *, role_id: int, principal_id: str, resource_id: str) -> None:
        response = await self._client.get(
            "/role-assignments", params={"principal_id": principal_id, "resource_id": resource_id}
        )
        response.raise_for_status()
        for assignment in response.json():
            if assignment["role_id"] == role_id:
                delete_response = await self._client.delete(f"/role-assignments/{assignment['id']}")
                delete_response.raise_for_status()

    async def revoke_resource_access(self, *, principal_id: str, resource_id: str) -> None:
        role_id = await self._ensure_member_role()
        await self._revoke(role_id=role_id, principal_id=principal_id, resource_id=resource_id)

    async def revoke_manager_access(self, *, principal_id: str, resource_id: str) -> None:
        """Phase 44 Session 2 (ADR 0163) - counterpart to `grant_manager_
        access`. Safe/idempotent to call even when the principal never
        held the manager role (e.g. `remove_member` calls this
        unconditionally for every departing member, manager or not) -
        `_revoke` is a no-op if no matching assignment exists."""
        role_id = await self._ensure_manager_role()
        await self._revoke(role_id=role_id, principal_id=principal_id, resource_id=resource_id)

    async def close(self) -> None:
        await self._client.aclose()
