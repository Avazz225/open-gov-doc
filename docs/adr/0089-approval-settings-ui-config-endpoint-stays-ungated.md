# 0089 — admin-ui: four-eyes settings page, `PUT /approval-config` deliberately stays ungated

**Status:** accepted (Post-roadmap Phase 22 Session 3)
**Context:** Post-roadmap Phase 22 Session 3, affects `admin-ui`, `permission-service`

## Decision

New admin UI page `/approval-settings/` (`ApprovalSettings.tsx`) lists all action types already
configured in `permission-service` (`GET /approval-config`) with a toggle per row (`PUT
/approval-config/{action_type}`), plus a form to create a not-yet-configured action type for the first
time via free text — `GET /approval-config` returns ONLY rows with an explicit `PUT` call, not a fixed
catalog of every action type existing in the system (see `docs/services/permission-service.md`).

While building this page, it was evaluated whether `PUT /approval-config/{action_type}` — so far the only
write endpoint in this topic area with no permission check at all — should be self-gated analogous to
`POST`/`PUT /roles` (ADR 0071, `admin.user_management`), now that it gets a UI for the first time.
**Outcome: evaluated, not implemented.** Research across the full test suite found that at least eight
services (`auth-service`, `config-service`, `folder-service`, `document-service`, `migration-service`,
`permission-service` itself, `workflow-service`, `webdav-connector`) call this endpoint directly as test
infrastructure — without `X-DMS-Principal`/capability, exactly the setup pattern that this project's newer
sessions (see e.g. post-roadmap Phase 21 Session 4's blast-radius analysis for `workflow-service`) now
routinely check before a breaking change.

## Rationale

- **Why no self-gating in this session**: self-gating would have touched over a dozen test call sites
  across the repo (each would need to add an `X-DMS-Principal` header with a matching capability) — a
  change of this scale doesn't belong in a session whose actual purpose is a new admin UI page. Same
  principle as the deliberate deferral in P21-S4 (there: response shape instead of a permission check),
  only this time the review led to deferring the change entirely rather than reshaping it.
- **Why a UI is built for it anyway, even though the endpoint stays ungated**: the configuration was
  previously only changeable via `curl`/a direct HTTP call — an admin UI page is a usability win
  regardless of the gating state (plan requirement for this session). The missing backend protection is
  an existing risk, not one worsened by this page (anyone with gateway access could already hit the
  endpoint via `curl` before) — the UI just makes the action more conveniently reachable, not newly
  possible. Deliberately **no** client-side `RequireCapability` dummy (unlike, e.g., `/users/`) — that
  would fake an enforcement that doesn't exist server-side; same discipline as `ArchivalTransfersView`'s
  retrieval button (there too, no UI gate where the backend has none).
- **Why `required_permission` is explicitly sent with every toggle**: `PUT
  /approval-config/{action_type}` always overwrites `required_permission` with the submitted value (even
  `null`, if omitted) — a plain "toggle requires_approval" call without this field would, e.g., have
  silently deleted `auth.superuser.activate`'s break-glass role binding (`breakglass.approve`).
  `ApprovalSettings.tsx`'s `handleToggle` therefore always sends the row's already-loaded
  `required_permission` value along, verified live against the real stack.

## Consequences

- **No backend code change** apart from an explanatory docstring on `put_approval_config` that points to
  this ADR, so a future session doesn't have to research this from scratch again.
- **Documented, still-open security point**: `docs/services/permission-service.md` "Open Points" names
  the risk explicitly (anyone with gateway access can toggle the four-eyes requirement for any action
  type) as a candidate for a future, dedicated hardening session that would also migrate the test call
  sites.
- **Tests**: `admin-ui` 185 (previously 179, +6: new `approval-settings.test.tsx` — empty state,
  unreachable state, listing sorted including `required_permission`/status, toggling including
  preservation of `required_permission`, creating a new action type, error display on toggle failure).
- **Verified live against the actual running stack** (image rebuild + restart of `admin-ui`, no code
  change needed in `permission-service`): a new action type `p22s3.test.action` was actually created via
  `PUT`, subsequently appeared in `GET /approval-config`; toggling with an explicitly submitted
  `required_permission=null` was confirmed; toggling `auth.superuser.activate` (real break-glass
  configuration) with an explicitly submitted `required_permission="breakglass.approve"` confirmed that
  its role binding is preserved rather than deleted — exactly the scenario that would have broken for
  real without explicitly sending it along. No interactive browser test (no browser/Playwright available
  in this development environment, project-wide established practice).
- Docs: new [ADR 0089](0089-approval-settings-ui-config-endpoint-stays-ungated.md),
  `docs/services/admin-ui.md` (page table, new section "Four-Eyes Settings",
  backend integration table, tests section), `docs/services/permission-service.md` ("Open Points"
  extended) updated.
