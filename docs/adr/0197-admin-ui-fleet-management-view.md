# 0197 — admin-ui: new Fleet Management view, fleet-management-service CORS + timing-safe operator key

**Status:** accepted
**Context:** P67-S1 (Phase 67, "Blocked on X, X Now Exists" — first session of the eighth gap-analysis
round's plan). The plan's own framing: `admin-ui`'s installation list (`lib/installations.ts`,
`InstallationManager.tsx`) is 100% `localStorage`, blamed on "the optional, not-yet-built Fleet/License
Management Service" — but `fleet-management-service` has fully existed since Phase 54 Session 1 with real
CRUD, and `apps/admin-ui/src` had zero references to it (confirmed independently by three separate
research agents during the round that produced this plan).

## Decision

**The plan's own premise needed correcting before implementation.** `lib/installations.ts`'s
`Installation` (`id`, `name`, `gatewayBaseUrl`) is Concept 8's multi-installation **admin login switcher**
— which gateway this browser's admin session talks to. `fleet-management-service`'s `ManagedInstallation`
(`id`, `display_name`, `gateway_base_url`, `fleet_agent_api_key`) is a completely different concept: an
installation a fleet **operator** remotely administers, with no login/RBAC of its own, gated only by a
shared operator secret. The two data models don't line up field-for-field, and conflating them (per the
plan's literal "wire `InstallationManager.tsx` to the real service" wording) would have meant merging two
genuinely unrelated concerns into one component. Instead: `lib/installations.ts`/`InstallationManager.tsx`
are left completely unchanged, and a **new**, separate `FleetManagementView.tsx` (own page,
`/fleet-management/`, own sidebar entry under the existing "Installationen" group) wires up
`fleet-management-service` — the same "new section added alongside, not folded into an unrelated
existing one" precedent `ProcessingFailuresView.tsx`'s `HandoverFailuresSection` already established for
federation-hub-service (another service with the identical "not registered with registry-service, no
gateway proxy, own operator-secret auth model" shape).

**Scope of the new view**: installation registration (shows the plaintext `fleet_agent_api_key` exactly
once, the "shown once" convention), listing, deletion, live status check (`GET
/installations/status`), and license push (`POST /installations/{id}/license`) — the concrete
"cross-device installation-provisioning round trip" the plan's own Definition of Done named. Groups,
update plans, and rollouts (the remainder of `fleet-management-service`'s API) are **not** built here —
a materially larger UI surface (four more entity types, a multi-step rollout-progress view) that would
have made this session unreasonably large; left as a scoped, not-yet-scheduled follow-up.

**Operator-key auth story**: reused the exact precedent named in the plan — `federation-hub-service`'s
`hub_operator_key`/`Authorization: Bearer <key>` pattern, already client-side-established in
`ProcessingFailuresView.tsx`'s `HandoverFailuresSection` (key kept only in component state, never
persisted, typed by a human operator per session). The one structural difference: fleet-management-service
gates **every** endpoint except `/healthz` (unlike the hub, which leaves plain reads ungated), so the new
view's list itself — not just individual mutating actions — only loads once a key is supplied.

**Incidentally discovered and fixed**: implementing this surfaced that `fleet-management-service` had
never been reachable from a browser before (confirmed by `docs/services/fleet-management-service.md`'s own
"no admin-UI currently calls this service" line) — so it had no CORS middleware at all, unlike
`federation-hub-service`, which already needed one for the exact same reason. The first live verification
attempt failed with a browser-side CORS rejection (masked as "Fleet Management Service nicht erreichbar",
confirmed working fine via `curl` in parallel) until this was added. While touching this function, also
switched `_require_operator_key`'s comparison from plain `!=` to `hmac.compare_digest` — the same
timing-safety fix `federation-hub-service`'s own `hub_operator_key` check already got at P62-S1, never
ported to this service's copy of the same pattern despite its own docstring naming that check as the
precedent to follow.

## Rationale

- **Why a new component instead of extending `InstallationManager.tsx`**: the two `Installation` concepts
  have no shared identity — the same physical DMS installation would need to appear as two entirely
  different rows (one keyed by an admin's own login preference, one keyed by an operator's fleet
  inventory) if merged, with no natural way to reconcile them. Keeping them separate matches
  `docs/services/admin-ui.md`'s existing precedent (ADR 0040's "Cross-Installation Config Compare" already
  deliberately keeps a third, related-but-distinct installation concept separate too).
- **Why not build groups/plans/rollouts in this session**: the plan's own Definition of Done specifically
  named "a live cross-device installation-provisioning round trip" as the verification bar — registration
  + status + license push covers that concretely. The remaining API surface is a genuinely separate,
  larger UI (multi-step rollout progress, four-eyes approve/reject on rollout steps) that deserves its own
  scoped session rather than being rushed into this one.
- **Why CORS was a real, necessary fix and not scope creep**: without it, the entire feature this session
  was asked to build would not work from a real browser at all — not an optional polish item.
- **Why fix the timing-safety gap while already touching this exact function**: same "incidental fix,
  discovered while touching adjacent code, documented transparently" pattern this project has used
  throughout the round (e.g. P66-S1's `reporting-service` `StorageClient` header bug) — the fix is one
  line, in the same function being edited for CORS-adjacent reasons, with zero risk of scope creep beyond
  this file.

## Consequences

- `apps/admin-ui/src/lib/config.ts`: new `FLEET_MANAGEMENT_SERVICE_BASE_URL`.
- `apps/admin-ui/src/lib/api.ts`: new `fleetManagementRequest()`, `ManagedInstallation`/
  `ManagedInstallationCreateOut`/`InstallationStatus` types, `listManagedInstallations`,
  `createManagedInstallation`, `deleteManagedInstallation`, `getManagedInstallationsStatus`,
  `pushInstallationLicense`.
- `apps/admin-ui/src/components/FleetManagementView.tsx` (new), `apps/admin-ui/src/app/fleet-management/page.tsx`
  (new), `apps/admin-ui/src/components/AdminSidebar.tsx` (new nav entry, no `requiresCapability` —
  fleet-management-service has no RBAC of its own).
- `apps/admin-ui/src/i18n/de.json`/`en.json`: new `nav.fleetManagement` + `fleetManagement.*` block (both
  locales kept in sync, this app's established convention).
- `apps/admin-ui/Dockerfile`, `infra/docker-compose.yml`: new `NEXT_PUBLIC_FLEET_MANAGEMENT_SERVICE_BASE_URL`
  build arg, same shape as the existing federation-hub one.
- `services/fleet-management-service/src/fleet_management_service/main.py`: `CORSMiddleware` registered
  (same origins as federation-hub-service's default); `_require_operator_key` now uses
  `hmac.compare_digest`.
- `services/fleet-management-service/src/fleet_management_service/settings.py`: new
  `cors_allowed_origins`.
- Tests: `apps/admin-ui/tests/fleet-management.test.tsx` (new, 7 tests, mocked API); full admin-ui vitest
  suite 290/290; `tsc --noEmit`/`eslint`/`next build` all clean; `services/fleet-management-service/tests/test_api.py`
  +1 (`test_cors_preflight_allows_admin_ui_origin`), 34/34.
- New `apps/admin-ui/e2e/fleet-management.spec.ts` (Playwright, against the real running stack): logs in,
  loads the fleet list with a real operator key, registers a real installation, asserts the one-time
  plaintext key is shown, asserts the row appears, deletes it, asserts it's gone. **Live-verified**: failed
  on the first run with the CORS rejection described above (masked as "service unreachable"), confirmed the
  root cause via `curl` succeeding in parallel, fixed, rebuilt, re-ran — passed cleanly. The operator key
  used for this (`DMS_FLEET_OPERATOR_KEY`) was set TEMPORARILY in `infra/docker-compose.yml` for this
  verification only, then removed again and the service redeployed — confirmed back to its fail-closed
  default (`403` for the same key that worked moments before), same precedent as P54-S1's own live
  verification.
- **Pre-existing, unrelated finding, not fixed here**: four of admin-ui's existing Playwright specs
  (`login.spec.ts`, `user-management.spec.ts`, `object-types.spec.ts`, `email-templates.spec.ts`) fail
  against the current stack — confirmed via trace inspection to be stale UI-text assertions (e.g.
  `login.spec.ts` looks for a nav link named "Nutzer & Rollen", the app's actual current text is
  "Nutzende & Rollen"; `user-management.spec.ts` looks for a form named "Nutzer anlegen", actual text is
  "Konto anlegen") — `de.json`'s copy was evidently reworded at some point without updating these E2E
  assertions to match. Confirmed unrelated to this session (the new `fleet-management.spec.ts` uses none
  of this stale text and passes cleanly; the vitest component-test suite, which doesn't depend on this
  exact text either, is fully green). Not fixed here — orthogonal to wiring up fleet-management-service,
  flagged for a future session.
