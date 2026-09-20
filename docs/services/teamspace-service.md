# teamspace-service

**Responsibility:** Team workspace "Teamspace" (Concept 2.5, P14-S6) — a self-managed, permanent group workspace (own folder/documents, shared appointments and contacts), deliberately distinct from the circulation folder (2.3, `case-service`): not a sequential routing/completion process, but a permanent group work area without a defined end. Any authenticated principal can create a new teamspace and invite members, with no administrative prior setup.

**Concept reference:** 2.5, 4.1, 8
**Own Postgres schema:** `teamspace` (tables `teamspace`, `teamspace_member`, `teamspace_appointment`, `teamspace_contact`)
**ADR:** [0043 — Dedicated membership table instead of RBAC extension, supplementary role assignment, no group support](../adr/0043-teamspace-service-membership-and-permission-integration.md)

## Architecture decision: own access regime, no own document store

`teamspace-service` has no own document/folder store (analogous to `case-service`'s opaque `document_id` references) — creating a teamspace creates a real `folder-service` folder (`parent_id="root"`, without `object_type_id`) and keeps its `id` as `root_folder_id`. The "access regime independent from the rest of the RBAC model" (Concept 2.5, verbatim) is a **dedicated, local membership table** (`teamspace_member`) — every endpoint except creation checks directly against this table (`main.py._require_member`/`_require_manager`), with no call to `permission-service` for the actual enforcement (this stays true after Post-Roadmap Phase 38 Session 4 — teamspace-service's OWN endpoints are unchanged). **In addition**, on every invite/removal the service creates or removes a resource-scoped `permission-service` role assignment (`teamspace-member`) on the root folder — since **Post-Roadmap Phase 38 Session 4** ([ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md)), this IS the primary, real access control for *direct* `folder-service`/`document-service` access (both now check `document.read`/`.write`/`folder.read`/`.write` for real, and the root folder is isolated via `inherit=False` so non-members can't reach it through the default-open "everyone" grant) — previously it only mattered to `search-service`. Full rationale, including the deliberate decision AGAINST group membership (a concept that is not actually enforced anywhere in the whole project): see ADR 0043.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/teamspaces` | Create (`name`, `description`) — any authenticated principal may, no capability gate. Automatically creates a `folder-service` root folder and makes the creator the first member (`can_manage_members=true`) |
| `GET` | `/teamspaces` | Only teamspaces the caller is a member of |
| `GET` | `/admin/teamspaces` | Installation-wide overview (since **Post-Roadmap Phase 22 Session 5**, [ADR 0090](../adr/0090-teamspaces-admin-overview.md)) — ALL teamspaces incl. `member_count`, independent of the caller's own membership. `403` without `X-DMS-Principal`/without the capability `admin.teamspace_management`, see below |
| `GET` | `/teamspaces/{id}` | Detail — `404` unknown, `403` not a member |
| `DELETE` | `/teamspaces/{id}` | Deletes only the teamspace metadata (members/appointments/contacts); the root folder remains — `403` without `can_manage_members` |
| `POST` | `/teamspaces/{id}/members` | Invite (`principal_id`, `can_manage_members`) — `403` without `can_manage_members`, `409` if membership already exists. Also creates the `permission-service` role assignment |
| `GET` | `/teamspaces/{id}/members` | Member list — any member may read |
| `PUT` | `/teamspaces/{id}/members/{principal_id}` | Change `can_manage_members` — `403` without own `can_manage_members`. Since **Phase 44 Session 2** ([ADR 0163](../adr/0163-teamspace-folder-delete-permission-and-orphan-resource-cleanup.md)), also grants/revokes the `teamspace-manager` `permission-service` role assignment (`folder.delete`) to match |
| `DELETE` | `/teamspaces/{id}/members/{principal_id}` | Removing one's own membership ("leave teamspace") is allowed for every member; removing others requires `can_manage_members`. Also removes the `permission-service` role assignment |
| `POST` | `/teamspaces/{id}/appointments` | Create appointment (`title`, `description`, `start_at`, `end_at`) — any member may |
| `GET` | `/teamspaces/{id}/appointments` | List, sorted by `start_at` |
| `DELETE` | `/teamspaces/{id}/appointments/{appointment_id}` | Any member may delete any appointment — fully shared, no creator-exclusive right |
| `POST` | `/teamspaces/{id}/contacts` | Create contact (`name`, `email`, `phone`, `note`) |
| `GET` | `/teamspaces/{id}/contacts` | List, sorted alphabetically |
| `DELETE` | `/teamspaces/{id}/contacts/{contact_id}` | Any member may delete |
| `GET` | `/healthz` | Health check |

Every gated endpoint requires the `X-DMS-Principal` header (set by the gateway from the JWT `sub` claim, see `gateway-service`) — `403` if missing.

## Data Model

- `teamspace`: `id` (UUID), `name`, `description`, `root_folder_id` (opaque `folder-service` reference), `created_by`, `created_at`/`updated_at`.
- `teamspace_member`: `id`, `teamspace_id` (FK), `principal_id` (Keycloak `sub` UUID, **no** group support, see ADR 0043), `can_manage_members`, `invited_by`, `invited_at`. Unique constraint on `(teamspace_id, principal_id)`.
- `teamspace_appointment`: `id`, `teamspace_id` (FK), `title`, `description`, `start_at`/`end_at`, `created_by`, `created_at`.
- `teamspace_contact`: `id`, `teamspace_id` (FK), `name`, `email`, `phone`, `note`, `created_by`, `created_at`. Deliberately a simple, teamspace-local address book entry — NOT the future, installation-wide "Contacts" special area (Concept 2.5, Phase 15, not yet built), see the concept table for the distinction.

## Integration with `folder-service`/`permission-service` (`clients.py`)

- `FolderServiceClient.create_folder()` — `POST /folders` with `parent_id="root"`, deliberately without `object_type_id` (the field is optional in `folder-service`, which entirely skips the validation that otherwise runs live against `object-type-service`).
- `PermissionServiceClient` — get-or-create of the `teamspace-member` role (permissions: `document.read`, `document.write`, `folder.read`, `folder.write`; since **Post-Roadmap Phase 38 Session 4** ([ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md)), all four are actually the real, working enforcement for direct `folder-service`/`document-service` access, not just for `search-service`) following the same pattern as `migration-service`'s `apply_role_assignment`. `grant_resource_access()`/`revoke_resource_access()` create/remove the assignment on the teamspace root folder. Since **Post-Roadmap Phase 32 Session 1** ([ADR 0130](../adr/0130-approval-config-self-gated-role-creation-four-eyes.md)), `POST /roles` requires `admin.user_management` — the client now attaches `X-DMS-Principal: teamspace-service` to every call, and `main.py._ensure_bootstrap_permissions` grants that principal `domain-admin-users` idempotently at startup (same pattern as `config-service`/`migration-service`). Since **Phase 44 Session 2** ([ADR 0163](../adr/0163-teamspace-folder-delete-permission-and-orphan-resource-cleanup.md)), a SECOND role, `teamspace-manager` (permissions: `folder.delete` only), is granted ADDITIONALLY to members with `can_manage_members=true` — `grant_manager_access()`/`revoke_manager_access()`, called from `create_teamspace` (creator), `invite_member` (if invited as a manager), `update_member` (promotion/demotion, idempotent either way), `remove_member`/`delete_teamspace` (unconditional revoke, no-op if never held). `folder-service`'s `DELETE /folders/{id}`/`POST /folders/{id}/trash` now check `folder.delete`, not the broader `folder.write` every member already has — without the second role, this would have left every ordinary member unable to delete anything, not just managers.
- **`ensure_isolated_resource()`/`restore_default_inheritance()`** (Post-Roadmap Phase 38 Session 4, ADR 0149) — `create_teamspace` calls `POST /resources` (synchronous, idempotent create-if-missing, same primitive as ADR 0144) then `PATCH /resources/{id}` with `inherit=False` on the root folder, so `permission-service`'s ancestor walk stops there instead of falling through to the now-broad "everyone" grant on ordinary folders. A startup-idempotent backfill (`main.py._ensure_teamspace_isolation_backfill`, using the new `repository.list_all_root_folder_ids`) applies the same to every teamspace created before this session. `delete_teamspace` calls `restore_default_inheritance()` (`inherit=true`) after revoking member access, so the kept root folder doesn't become permanently unreachable.

## Integration with `auth-service`: `GET /users/lookup`

Inviting requires resolving a typed username into the actually authoritative Keycloak `sub` UUID (`X-DMS-Principal`/`RoleAssignment.principal_id`). The existing `GET /users` in `auth-service` is gated behind `admin.user_management` — unsuitable for teamspaces, since everyone should be able to invite. New, narrower endpoint `GET /users/lookup?username=` (see `docs/services/auth-service.md`): exact name search, any authenticated user, returns only `{id, username}` (no general people directory).

## Installation-wide admin overview (Post-Roadmap Phase 22 Session 5, [ADR 0090](../adr/0090-teamspaces-admin-overview.md))

`GET /admin/teamspaces` — unlike `GET /teamspaces` (membership-filtered,
`repository.list_teamspaces_for_principal`), this endpoint returns **all** teamspaces
(`repository.list_all_teamspaces_with_member_counts`, `outerjoin` + `GROUP BY` instead of a filter),
including `member_count` per row instead of a full member list (a full list would require
`GET /teamspaces/{id}/members`, which requires `_require_member` — disproportionate for a
pure admin overview endpoint). Gated via a new `PermissionServiceClient.
has_permission()` check (`admin.teamspace_management`, new pre-seeded
domain `domain-admin-teamspaces` at `permission-service`, see its own docs, "Domain-separated
admin roles") — the first real permission check in this service (the remaining endpoints check
exclusively against the service's own `teamspace_member` table, `_require_member`/`_require_manager`,
no cross-service call for authorization). Teamspaces themselves remain unchanged, self-managed (2.5) —
no capability gate for creation/joining, only this new overview.

## user-ui integration

New `TeamspacesPane.tsx` (icon rail entry 👥) — master-detail view: list of own teamspaces + creation form on the left, members/appointments/contacts + "open folder" (navigates into the regular document explorer; `root_folder_id` is resolved once for this via `GET /folders/{id}`, the same principle used when opening a favorited folder, P7-S1d) in the detail area on the right. Inviting first calls `lookupUserByUsername()`, then `inviteTeamspaceMember()` with the resolved UUID.

## Self-registration (Concept 3.2a)

Registers itself with the registry at startup (`libs/dms-registry-client`), the same pattern as every other service. Gateway routing runs entirely dynamically via `service_type="teamspace-service"`, no own gateway code change needed.

## Events

Publishes (stream `teamspace`):

| event_type | payload |
|---|---|
| `teamspace.created` | `{name, root_folder_id}` |
| `teamspace.deleted` | `{}` |
| `teamspace.member_invited` | `{principal_id}` |
| `teamspace.member_removed` | `{principal_id}` |

Deliberately no appointment/contact events — the membership events are the security-relevant ones (who can see this area), appointments/contacts are purely business data without comparable audit relevance.

**Since P55-S1** ([ADR 0175](../adr/0175-teamspace-service-root-folder-deletion-cleanup-consumer.md)): this service gained its first consumer, `consumer.py`, subscribing to `folder-service`'s `folder.resource.deleted` (a second, separate `NatsEventBusClient` connection alongside the existing producer, same dual-bus pattern as `case-service`). When the deleted folder's ID matches a teamspace's `root_folder_id`, every member's `permission-service` access on it is revoked and the teamspace (plus its members/appointments/contacts) is torn down via the same `repository.delete_teamspace` the manual `DELETE /teamspaces/{id}` path uses — closing the orphan this service would otherwise be left with when a manager deletes the root folder directly via `folder-service` (see "Open Points" below). Every other `folder.resource.deleted` event (the vast majority — this fires for every folder deletion in the installation) is a silent no-op, since no matching teamspace exists.

**Audit integration**: The Audit Service has additionally consumed `teamspace.>` since this session.

## Tests

- `uv run pytest services/teamspace-service/tests`: repository (creation incl. creator as first member, duplicate rejection, cascading deletion of members/appointments/contacts when a teamspace is deleted, member/appointment/contact CRUD, `NotFoundError` cases incl. cross-teamspace confusion). API (runs against the real, running `folder-service`/`permission-service`, no mocking): creation creates a real folder and grants the creator a real `permission-service` role assignment, membership check (`403` for non-members), invite/remove incl. `permission-service` anchoring (assignment actually appears/disappears), self-removal without `can_manage_members` possible, removing others requires it, appointments/contacts CRUD. **53 tests since P55-S1** (+2, new `test_consumer.py`: the `folder.resource.deleted` handler tears down a matching teamspace incl. revoking every member's access, and is a no-op for an unrelated folder's deletion — `make_handler`'s returned `handle()` called directly with a real `Event` against a real DB, no real NATS round-trip needed, a `FakePermissionClient` records calls, same pattern as `case-service/tests/test_consumer.py`). Before P55-S1, 51 tests since Phase 44 Session 2 (previously 45, +6: a non-manager member gets `403` from `folder-service` directly on both `DELETE /folders/{id}` and `POST /folders/{id}/trash` for the teamspace root, a manager (invited as one) can trash+restore it, promoting/demoting via `PUT .../members/{id}` grants/revokes `folder.delete` accordingly, removing a member revokes the `teamspace-manager` role assignment too). Before that, 45 tests since Post-Roadmap Phase 22 Session 5 (previously 41, +4: `GET /admin/teamspaces` without principal/without capability → `403` each, an end-to-end test across two teamspaces with different creators confirms that a non-member with the capability sees both incl. correct member counts, plus a repository unit test for `list_all_teamspaces_with_member_counts`).
- **Live verification this session**: `docker compose up -d --build teamspace-service` against the full stack, self-registration with `registry-service` confirmed (`GET /instances/teamspace-service`), gateway routing confirmed (`POST /api/teamspace-service/teamspaces` without a token → `401`, proving correct resolution via the generic `/api/{service_type}/...` proxy).
- **Real end-to-end browser verification** (ephemeral Playwright container, see `docs/services/user-ui.md` "Team workspaces" section for the network stumbling block found in the process): login → create teamspace → open detail → invite member by username (correctly resolves via `GET /users/lookup`) → create appointment/contact → open folder switches into the document explorer → logged in as an invited, non-management member, confirmed: delete/invite actions are invisible, "leave" works → deleting as a management member removes the teamspace. No console errors. Found and fixed a UI bug in the process (`user-ui`'s delete buttons for appointments/contacts showed "common.delete" instead of "Löschen" — missing i18n key).
- **Live verification, P55-S1**: created a real teamspace via the API, confirmed two real `permission-service` role assignments existed for its creator on the root folder, then deleted that root folder directly via `folder-service`'s `DELETE /folders/{id}` (bypassing this service entirely — the exact bypass this session closes), and confirmed against the real, rebuilt container: `GET /teamspaces/{id}` now `404`s, both role assignments are gone, and the container's own log shows `teamspace_orphan_cleanup_completed` — where before this session, the teamspace row and both grants would have persisted forever.

## Open Points

- **No protection against the last management-capable member removing themselves** — a teamspace can thereby become unmanageable (nobody can invite/remove/delete anymore). Deliberately not handled (rare edge case).
- ~~**No group membership** — Concept 2.5 names it as an option, but no enforced group concept exists anywhere in the project (neither a Keycloak group claim nor `permission-service` group expansion).~~ — **partially stale, re-scoped in Post-Roadmap Phase 43 Session 2** ([ADR 0160](../adr/0160-teamspace-group-invitation-scoping.md), scoping only, no code changed): a Keycloak `groups` claim IS now a real, actively-consumed concept since P24-S2/ADR 0093 (group→role mapping, resolved live at `GET /me`) — but that mapping is role-only and never exposes a raw, queryable "who is currently in group X" member list, which is the specific piece teamspace group invitation would actually need. Group membership therefore remains genuinely unbuilt here, for a more precisely scoped reason than the original wording — see ADR 0160 for the concrete build recommendation. `permission-service` group expansion (`RoleAssignment.principal_type="group"`) remains entirely unenforced, unchanged. See ADR 0043/0160.
- ~~**`permission-service` anchoring is not the primary enforcement** — only `search-service` actually checks it today. Direct `folder-service`/`document-service` access to the teamspace folder is NOT protected by teamspace membership (an already project-wide documented gap, not new here, just not closed).~~ — **resolved in Post-Roadmap Phase 38 Session 4** ([ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md)): `folder-service`/`document-service` now check real `document.read`/`.write`/`folder.read`/`.write` on their core paths, and the teamspace root folder's `ResourceNode` is set `inherit=False` (`ensure_isolated_resource`, called from `create_teamspace` plus a startup backfill for pre-existing teamspaces) so "everyone"'s now-broad grant on those same four permissions doesn't reach inside — this `teamspace-member` role assignment is the real, working, primary enforcement for direct access now, not just an anchor for `search-service`. ~~**Residual, accepted gap**: `teamspace-member`'s `folder.write` still lets a regular (non-manager) member bypass this service's own manager-only `DELETE /teamspaces/{id}` guard via `folder-service`'s `DELETE /folders/{root_folder_id}` directly...~~ — **closed in Phase 44 Session 2** ([ADR 0163](../adr/0163-teamspace-folder-delete-permission-and-orphan-resource-cleanup.md)): `folder-service` now distinguishes exactly this, via a dedicated `folder.delete` permission (see "Integration with `folder-service`/`permission-service`" above) that only the new `teamspace-manager` role carries.
- **Deletion removes only teamspace metadata, not the root folder** — a deliberate boundary; real folder deletion would be a standalone feature (retention, four-eyes principle, 5.2). Since **Post-Roadmap Phase 38 Session 4** ([ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md)), deletion also resets the kept folder's `inherit` flag back to `true` (`restore_default_inheritance`) — without this, the kept folder would stay `inherit=False` forever with every member's grant just revoked, i.e. permanently unreachable by anyone, not even its creator. The reset makes it behave like any other ordinary folder again.
- **`GET /users/lookup` is an existence oracle** — any authenticated user can find out whether a given username exists. Classified as uncritical for internal administrative software with a known user population.
- ~~No admin UI access — teamspaces are a pure end-user feature in `user-ui`, no management view in `admin-ui` (e.g. "list all teamspaces of an installation") planned~~ — **fixed in Post-Roadmap Phase 22 Session 5** ([ADR 0090](../adr/0090-teamspaces-admin-overview.md)): new `GET /admin/teamspaces` endpoint + new `admin-ui` page `/teamspaces/`, gated via the new capability `admin.teamspace_management`. Still pure visibility — no administrative actions (deletion/member management) from the admin UI, that remains self-management.
