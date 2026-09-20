# 0171 — AD group→role mapping: default-role four-eyes, initiator display-name fix

**Status:** accepted (Phase 53 Session 1, see Phase 53 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 53 Session 1 ("Lower-Priority Hardening & Polish"), affects `auth-service` and
`admin-ui`. Two small residuals [ADR 0153](0153-ad-group-mapping-composite-rules-default-role-four-eyes-export.md)
itself named and neither touched by P50-S5 (which only added this feature's admin-UI CRUD page).

## Decision

**1. `PUT /ad-group-mappings/default-role` gains the same optional, per-installation four-eyes gate the
other four AD-group-mapping mutations already have.** ADR 0153 deliberately left this single scalar
setting ungated ("gating it would be scope beyond what was asked, not a gap this session leaves open by
oversight"), accepting the residual risk explicitly: *"a compromised `admin.user_management` account
could still silently grant a broad default role to every otherwise-unmapped AD group member without a
second approver."* This session reverses that scope boundary on explicit request, using the exact same
`_maybe_defer_to_approval`/consumer-execution pattern already proven for the other four mutations, new
action type `auth.ad_group_mapping.default_role_set`. The endpoint's response is now the same
`{status, config, approval_request_id}` envelope shape as the sibling create endpoints — `GET` is
unchanged, plain, never four-eyes-relevant.

**2. A mapping/rule/default-role created or set via the four-eyes/consumer path now records the
initiator's resolved username as `created_by`/`updated_by`, not their raw Keycloak `sub`.** ADR 0153's
own wording ("the approver's raw Keycloak `sub`") was itself imprecise — the code actually stores the
raw `initiated_by` (the person who FILED the request), not `approved_by` (the person who approved it);
this ADR's own precise reading of the code confirms it. Fixed with a new `consumer._resolve_display_name()`
helper that reuses the ALREADY-ESTABLISHED reverse-identity-resolution primitive (`admin_users.
find_user_by_id`, `GET /users/{user_id}`, ADR 0069 — the same mechanism delegations/teamspace member
lists already use to turn a raw Keycloak UUID into a username for display), called server-side at
execution time. Falls back to the raw id on a 404 (e.g. a `TechnicalAccount` initiator's `sub` is a
local integer row id, not a Keycloak UUID, and never resolves) - same fallback shape the direct/ungated
path already has (`user.get("preferred_username") or principal_id`).

## Rationale

**Why the display-name fix does NOT touch `initiated_by`/`approved_by` themselves.** Both remain raw
principal ids end-to-end, in the approval-request payload, in the NATS event, and in `permission-service`'s
own `ApprovalRequest` model. Changing `initiated_by` to a display string at request time was considered
and rejected: `initiated_by` and `approved_by` are both currently raw Keycloak `sub`s, and any future or
existing self-approval check (comparing the two) depends on them being the same kind of value. Resolving
only at the point `created_by`/`updated_by` gets written — a pure display-layer concern — avoids that
risk entirely and needed no change anywhere in `permission-service` or to the approval-request schema
itself. This is deliberately the SMALLER fix ADR 0153 itself contrasted against ("extending the approval
payload to also carry a display name would be a larger change... not attempted here") — a plain
server-side lookup at execution time needed neither.

**Why `admin-ui` needed a matching change.** `setAdGroupMappingDefaultRole()`'s return type changes from
the plain `AdGroupMappingDefaultRole` resource to the new envelope — `AdGroupMappings.tsx`'s
`handleSaveDefaultRole` now branches on `status`, mirroring the identical `pending_approval` handling its
own mapping/composite-rule create handlers already have (no new pattern introduced, just extended to a
fifth call site).

## Consequences

- **New action type**: `auth.ad_group_mapping.default_role_set`, added to `consumer.py`'s
  `_KNOWN_ACTION_TYPES` and its own execution branch.
- **`consumer.make_handler`/`start_consuming` gain a new required `keycloak_admin` parameter** (a
  `KeycloakAdmin` client, already built once at `main.py`'s own lifespan startup and now also threaded
  into the consumer) - needed for the display-name resolution, not used for anything else in this
  consumer.
- **Response shape change**: `PUT /ad-group-mappings/default-role` now returns
  `AdGroupMappingDefaultRoleActionResult` instead of the plain `AdGroupMappingDefaultRoleOut` - a
  breaking change for any existing direct caller of this one endpoint (none exist outside `admin-ui`,
  updated in the same session).
- **Regression tests**: `test_ad_group_mapping.py` (+1, the new four-eyes-deferral test; the existing
  get/set/reset test updated for the new envelope shape), `test_consumer.py` (all eight existing tests
  updated to pass the new `keycloak_admin` fixture parameter — a real `KeycloakAdmin` client against the
  real dev Keycloak, where the fake `"alice"`/`"bob"` test identities correctly 404 and fall back to the
  raw id, leaving every existing assertion unchanged), `ad-group-mappings.test.tsx` (+1, the new
  pending-approval test; the existing default-role save test updated for the new envelope shape).
