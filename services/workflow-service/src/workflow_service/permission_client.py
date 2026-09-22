from datetime import datetime

from dms_permission_client import PermissionServiceClient as _BasePermissionServiceClient


class PermissionServiceClient(_BasePermissionServiceClient):
    """workflow-service's local extension of the shared `dms-permission-client`
    base (P68-S2 migration) - keeps only the methods with no shared-lib
    equivalent: `check_delegation` (deliberately excluded from the shared
    package per ADR 0066 - a service-specific extra), plus
    `create_org_hierarchy_grant`/`revoke_org_hierarchy_grant`/
    `is_supervisor_of` (org-hierarchy dynamic access grants, Post-Roadmap
    Phase 31 Session 10/P66-S2, likewise not shared-lib material).
    `has_permission`/`check`/`is_maintenance_active`/`close` now come from
    the shared base unchanged; `ROOT_RESOURCE_ID` likewise."""

    async def check_delegation(
        self,
        *,
        deputy_principal_id: str,
        delegator_principal_id: str,
        process_definition_id: int,
        object_type_id: int | None = None,
        folder_resource_id: str | None = None,
        case_resource_id: str | None = None,
    ) -> bool:
        """Deputizing during absence (4.4a, P14-S11) - true if
        ``deputy_principal_id`` is currently registered as an active
        deputy for ``delegator_principal_id`` (time window + optional
        process/object-type/folder/case scope), see main.py's
        ``complete_task``. ``object_type_id``/``folder_resource_id``
        activate the previously dead scope dimensions since P32-S2 (ADR
        0048's own anticipated "additional resolution step") - see
        main.py's ``_resolve_business_key_scope``, which resolves them
        (and, since Post-Roadmap Phase 39 Session 4/ADR 0154,
        ``case_resource_id``) from the instance's ``business_key`` before
        this call."""
        params: dict[str, str | int] = {
            "deputy_principal_id": deputy_principal_id,
            "delegator_principal_id": delegator_principal_id,
            "process_definition_id": process_definition_id,
        }
        if object_type_id is not None:
            params["object_type_id"] = object_type_id
        if folder_resource_id is not None:
            params["folder_resource_id"] = folder_resource_id
        if case_resource_id is not None:
            params["case_resource_id"] = case_resource_id
        response = await self._client.get("/delegations/check", params=params)
        response.raise_for_status()
        return bool(response.json()["allowed"])

    async def create_org_hierarchy_grant(
        self,
        *,
        principal_id: str,
        grant_kind: str,
        process_definition_id: int,
        ends_at: datetime,
        case_resource_id: str | None = None,
    ) -> dict:
        """Dynamic org-hierarchy-based temporary access grant (14.2,
        Post-Roadmap Phase 31 Session 10) - resolves ``principal_id``'s
        supervisor(s)/chain/org-unit at permission-service and auto-creates
        one ``Delegation`` per resolved deputy. Returns the raw response
        dict (``{"delegation_ids": [...], "deputy_principal_ids": [...]}``)
        - callers need both: the delegation IDs to store for later
        revocation (``main.py``'s ``TaskClaim.granted_delegation_ids``) and
        the deputy IDs to report back to whoever requested the grant.
        ``case_resource_id`` (Post-Roadmap Phase 39 Session 4, ADR 0154) -
        when the triggering instance's ``business_key`` resolved to a real
        case, narrows the grant to that specific case in addition to the
        process-definition family."""
        response = await self._client.post(
            "/org-hierarchy-grants",
            json={
                "principal_id": principal_id,
                "grant_kind": grant_kind,
                "process_definition_id": process_definition_id,
                "ends_at": ends_at.isoformat(),
                "case_resource_id": case_resource_id,
            },
        )
        response.raise_for_status()
        return response.json()

    async def revoke_org_hierarchy_grant(self, delegation_id: str) -> None:
        """Cleanup counterpart (Post-Roadmap Phase 31 Session 10) - ends an
        auto-created delegation early instead of waiting for its backstop
        `ends_at`, called when a claim is released or its task completes.
        Tolerant of an already-revoked/unknown ID (`404`) - best-effort
        cleanup must not fail the primary action (claim release/task
        completion) that triggered it."""
        response = await self._client.delete(f"/org-hierarchy-grants/{delegation_id}")
        if response.status_code == 404:
            return
        response.raise_for_status()

    async def is_supervisor_of(self, principal_id: str, *, of_principal_id: str) -> bool:
        """P66-S2: read-only supervisor check for `reassign_task`'s new
        authorization gate - reuses `GET /supervisor-chain/{principal_id}`
        (P31-S9, the same transitive-chain lookup `create_org_hierarchy_grant`'s
        "supervisor chain" grant kind already resolves internally), rather
        than `create_org_hierarchy_grant` itself, which has the side effect
        of creating a `Delegation`. `supervisor_ids` is the full transitive
        chain, not just the direct supervisor, so a skip-level supervisor is
        also authorized."""
        response = await self._client.get(f"/supervisor-chain/{of_principal_id}")
        response.raise_for_status()
        return principal_id in response.json()["supervisor_ids"]
