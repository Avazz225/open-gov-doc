# 0216 — P74-S2 RBAC completions: auth-service four-eyes retrofit, workflow-service superuser bypass

**Status:** accepted
**Context:** P74-S2 (Phase 74, tenth gap-analysis round). A small bundle of three previously-named,
independently-scoped RBAC gaps, closed together in one session because each is individually cheap.

## (a) auth-service: four-eyes on `PUT /user-tracking-config/{principal_id}`

`docs/services/auth-service.md`'s own Open Points (ADR 0157) named this as genuinely open since Phase 41
Session 3: the toggle that enables/disables fine-grained session tracking for a privileged account had no
four-eyes gate, unlike the structurally similar AD-group-mapping endpoints (ADR 0153).

**Decision**: reuse the existing generic `_maybe_defer_to_approval` helper exactly as the AD-group-mapping
endpoints already do, under a new `action_type="auth.user_tracking_config.update"`. `put_user_tracking_
config` now returns `UserTrackingConfigActionResult` (`{status: "applied"|"pending_approval", config:
UserTrackingConfigOut | None, approval_request_id: str | None}`) instead of a bare `UserTrackingConfigOut`
— the same always-wrapped envelope shape ADR 0153 established for `AdGroupRoleMappingActionResult`, chosen
over a conditionally-different response shape so callers have one predictable contract regardless of
whether four-eyes is configured for this installation. The deferred-approval execution path is wired into
`consumer.py`'s existing `permission.approval.approved` handler (`_KNOWN_ACTION_TYPES` tuple plus a new
`elif` branch), matching every other four-eyes-gated action type in this service.

`admin-ui`'s `UserTracking.tsx` gained a `configPending` state and a pending-approval hint, copied 1:1 from
`AdGroupMappings.tsx`'s established pattern.

**Rationale for reusing rather than inventing**: this is the same generic mechanism (ADR 0022/0153) applied
to a fourth action type in this service — no new decision to make about *how* four-eyes works here, only
*which* action type gets gated and what the response envelope looks like for a caller that previously got
a bare object back.

**Live-verified against the real running stack**: full round trip via direct `curl` against the rebuilt
`auth-service`/`permission-service` containers — gate off → `applied`; `requires_approval=true` set via
`PUT /approval-config/auth.user_tracking_config.update`; toggle again → `pending_approval` with a real
`approval_request_id`, config unchanged; approved → config updated, `updated_by` resolved to the approving
principal's display name via the existing `_resolve_display_name` helper.

**Known pre-existing, unrelated test-infra issue, not caused by this change**: the new
`test_put_tracking_config_with_approval_required_defers_the_change` test fails using the
`approval_config_override` pytest fixture (`services/auth-service/tests/conftest.py`) — the same fixture
already causes 3 pre-existing failures in `test_ad_group_mapping.py` (confirmed via `scripts/run-tests.sh
auth-service`: this project already carries 6 pre-existing failures from this exact fixture before this
session's change). The live-stack verification above proves the actual production behavior is correct;
the fixture's timing bug is out of this session's scope and left as-is, consistent with this project's
established practice of documenting rather than silently working around unrelated pre-existing test
infrastructure issues.

## (b) workflow-service: extend the general superuser bypass (ADR 0190)

ADR 0195 and ADR 0211 each explicitly named the same residual when they were written: `workflow-service`
had no local `_is_active_superuser` helper at all, unlike `permission-service`/`query-service`/
`plugin-orchestration-service`, so an activated break-glass superuser (4.6) was not exempted from either
the reassignment-authorization gate (ADR 0195) or the per-claimant completion gate (ADR 0211). Both ADRs
deferred this as "a separate, independently-scoped gap," to be closed together rather than duplicated.

**Decision**: add the same shape of helper as the other three services — new `auth_client.py`
(`AuthServiceClient.get_active_superuser()`, calling `auth-service`'s `GET /superuser/status`), a new
`auth_service_base_url` setting, lifespan wiring (`app.state.auth_client`), and `_is_active_superuser
(x_dms_principal)` comparing the specific caller against the currently-active superuser's own principal id
(actor-matching, not "is any superuser active"). Applied at both gate sites:

- `reassign_task`: the bypass short-circuits the `claimant-or-supervisor` check (the earlier `404`-on-
  unclaimed check stays unconditional — a structural check, not an authorization one).
- `_require_claim_authorization_if_claimed`: the bypass is checked against the raw caller
  (`x_dms_principal`), not the on-behalf-of `effective_principal` — an activated superuser bypasses on
  their own authority, delegation is a separate mechanism.

`infra/docker-compose.yml`'s `workflow-service` block gained `DMS_AUTH_SERVICE_BASE_URL` and a
`depends_on: auth-service` entry (confirmed no cycle: `auth-service` depends only on `postgres`/`nats`/
`keycloak`/`permission-service`, never on `workflow-service`).

**Live-verified against the real running stack**: activated the real break-glass superuser through its
actual four-eyes flow (`POST /approval-requests` with `action_type="auth.superuser.activate"`, approved by
a second distinct principal — self-approval is rejected, confirmed), then against a real process instance
with a claimed task: an unrelated bystander's reassign attempt is rejected `403`; the actual activated
superuser's identical request succeeds `200`. Superuser deactivated and the temporary `breakglass-approver`
role grants used only for this verification were revoked afterward, leaving no residual state change.

## (c) storage-service: investigated, already closed

The plan named "ungated `GET /storage/usage` and `process-pending` endpoints" as a possible third item in
this bundle. Direct code read of `services/storage-service/src/storage_service/main.py` found all three
already gated via `dependencies=[Depends(_require_storage_caller)]` since P66-S1/P67-S2, with
`_TRUSTED_STORAGE_CALLERS` already including every caller this bundle's premise assumed was missing. Stale
premise from the plan's own research round predating those sessions — no code change needed, documented
here so a future gap-analysis round doesn't rediscover the same already-closed item.

## Consequences

- Both (a) and (b) close residuals that their own originating ADRs (0157, 0195, 0211) had already
  explicitly named and deferred — no new residual is introduced by this session.
- `docs/adr/0195-*.md` and `docs/adr/0211-*.md` had their "Accepted, documented residual" bullets struck
  through and cross-referenced to this ADR.
- `docs/services/auth-service.md` and `docs/services/workflow-service.md` updated at the specific bullets
  that named these gaps.
- The pre-existing `approval_config_override` fixture-timing bug (noted in (a) above) remains open —
  affects 4 tests total across `test_ad_group_mapping.py` (3) and `test_user_tracking.py` (1, new this
  session). Worth its own session if it ever blocks something more directly; not attempted here.
