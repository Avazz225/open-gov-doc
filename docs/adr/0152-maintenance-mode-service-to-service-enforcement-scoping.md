# 0152 — Maintenance mode vs. direct service-to-service writes: Scoping (no implementation)

**Status:** accepted
**Context:** P39-S2 (Post-Roadmap Phase 39), third of three named "remaining four-eyes/validation
gaps" (see `IMPLEMENTATION_PLAN.md` "Phase 39"). Presented to the user as three options
(leave as documented / scope it properly first / build a real fix now) — the user explicitly chose
**"Scope it properly first"**, mirroring the [ADR 0147](0147-cross-installation-xdomea-handoff-scoping.md)/
P37-S1 precedent: a scoping-only session with a concrete build recommendation for a future session, no
implementation. [ADR 0024](0024-not-shutdown-gateway-enforced.md) already documents the gap explicitly
and accepts it ("A generic 'block all write paths of all services' mechanism would be its own,
significantly larger session"), tied to [ADR 0005](0005-gateway-registry-routing-and-inprocess-rate-limiting.md)'s
separate, pre-existing fact that no service-to-service authentication exists anywhere in this project —
backend services "implicitly trust that they are only reached via the gateway." This ADR does the
scoping work ADR 0024 itself deferred: a concrete inventory of what's actually exposed, and a concrete,
buildable recommendation.

## Findings

**The gap splits into two categories of materially different severity**, found by enumerating every
direct (non-gateway-proxied) service-to-service HTTP write call in the codebase:

- **Category A — cascading writes inside an otherwise gateway-checked request.** The inbound request
  that triggers the cascade DID pass through the gateway's maintenance check, but the downstream
  service-to-service write it triggers does not re-check. Examples: `document-service` →
  `storage-service` (object PUT/DELETE, `storage_client.py:49,64`), → `virus-scan-service` (`POST
  /scans`, `virus_scan_client.py:48`), → `rendering-service` (export/stamp/redact,
  `rendering_client.py:39,66,83,95,112,130,143`); `folder-service` → `document-service` (cascade
  trash/restore, `document_client.py:18,30`); `case-service` → `workflow-service` (`POST /instances`);
  `migration-service` → `permission-service`/peer installations/`folder-service`; `signature-service` →
  `document-service` (checkin-signed-version).
- **Category B — writes fired from a background poll loop, with NO gateway involvement, ever.**
  Materially worse: these run on a timer regardless of maintenance-mode state, independent of any
  inbound request. Found in `archival-service._archival_poll_loop` (disposal writes: mark_archived/
  mark_dehydrated/mark_rehydrated, storage delete), `folder-service._retention_poll_loop` and
  `document-service._retention_poll_loop`/`_lock_reminder_poll_loop` (forced retention deletion),
  `mail-connector._poll_loop` (a full document-creation pipeline off a POP3 timer), and retry loops in
  `ocr-service`, `rendering-service`, `federation-hub-service`, `reporting-service`. Several of these —
  archival disposal, retention forced-deletion, mail-triggered document creation — are exactly the kind
  of write an emergency lockdown exists to stop.
- **`workflow-service` already solves this correctly for itself**: its own two poll loops call
  `PermissionServiceClient.is_maintenance_active()` (`permission_client.py:54-57`) before proceeding
  (`main.py:163,211`), in addition to the gateway's header-based check for its proxied endpoints. This
  is the only existing precedent in the codebase and the template a fix should copy.
- **`libs/dms-permission-client`** (the shared client library already used by several services, extracted
  to deduplicate `PermissionServiceClient` code across services) has no `is_maintenance_active()`/
  maintenance-mode method today — `check`/`check_batch`/`has_permission`/`create_resource_node`/
  `get_role_id`/`ensure_role_assignment` exist, maintenance-mode does not.
- **No service mesh/sidecar exists or is planned**: `infra/docker-compose.yml` is plain bridge
  networking; the one Helm chart under `infra/k8s` (ADR 0099) is generic and values-driven with no
  Istio/Linkerd/mTLS sidecar model. Enforcing this at a network layer instead of application code is
  not plausible given the current deployment model.
- **No later ADR has revisited this** — a grep across `docs/adr/` for "maintenance mode"/
  "service-to-service" post-0024 turns up no session that touched this gap; it has sat exactly as
  ADR 0024 left it.

## Recommendation (for a future build session, not this one)

**Extend `libs/dms-permission-client` with an `is_maintenance_active()` method** (mirroring
`workflow-service`'s own already-working `permission_client.py` pattern almost verbatim: a thin `GET
/maintenance-mode` call against `permission-service`, no caching needed given the low call frequency of
poll loops), then **roll it out to Category B's poll loops first** (archival-service, folder-service,
document-service's two loops, mail-connector, ocr-service, rendering-service, federation-hub-service,
reporting-service — roughly 9 call sites across 8 services) as the higher-priority half of the fix,
since these run with zero gateway involvement ever and include the two clearest cases (retention forced
deletion, mail-triggered document creation) that ADR 0024's use case (4.8, "halt running... scheduled
jobs") already names as literally in scope but never implemented for anything but `workflow-service`'s
own SLA loop. Category A's request-triggered cascades are the lower-priority second half — the inbound
request was already checked once, so the residual risk is a maintenance-mode toggle happening mid-cascade,
a narrower race window than Category B's permanent exposure — and could reasonably be addressed by
having each internal `*_client.py` forward the `X-DMS-Maintenance-Active` header it already received on
the inbound request, rather than an extra query, avoiding a second round trip per cascade step.

A generic, blanket "block every write path of every service" mechanism remains explicitly **not**
recommended, for the same reason ADR 0024 gave: it would require either inventing service-to-service
authentication from scratch (a much larger, independent architectural change with no other driver in
this project today) or trusting an unauthenticated header/query result as an authorization signal across
every write path in the system, which is a bigger trust expansion than the two targeted additions above.
Both recommended additions stay within the existing trust model (ADR 0005: services already implicitly
trust their internal network position; `is_maintenance_active()` only adds a read of already-public
system state, not a new privilege).

## Consequences

- **No code changes in this session.** `PROGRESS.md` marks P39-S2's third item explicitly as "Scoping,
  kein Feature", per the same convention P37-S1 established.
- **The gap remains open until a future build session implements the recommendation above** — Category
  B's poll-loop writes remain fully unaffected by maintenance mode in the meantime, exactly as they are
  today.
- **A future build session has a concrete starting point**: extend `libs/dms-permission-client`, copy
  `workflow-service`'s existing pattern into the ~9 identified Category B call sites, and only then
  decide whether Category A's header-forwarding variant is worth the same session's scope or a separate
  one.

**Update, Phase 51 Session 4**: Category B was closed in Phase 44 Session 3 (ADR 0164). This session
closed Category A for the two services the follow-up plan explicitly named — `document-service`
(`create_document`, `checkin_version`, `redact_document` — the three request-triggered endpoints that
cascade into `storage-service`/`virus-scan-service`/`rendering-service`) and `folder-service`
(`trash_folder`/`restore_folder`, cascading into `document-service`). Implemented as a header check
(`X-DMS-Maintenance-Active`, already forwarded by the gateway) right in each endpoint's own handler via
a local `_reject_during_maintenance` helper — the same shape `workflow-service` already established at
P6-S6, copied rather than reinvented once it turned out to already exist in this codebase (this ADR's
own "Findings" section named it "the only existing precedent" without registering it also already
covers Category A, not just B). A deliberate, narrower implementation than this ADR's literal
recommendation ("each internal `*_client.py` forward the header downstream"): rejecting in the calling
service's own handler achieves the same protection (the cascade never starts) without needing to modify
`storage-service`/`virus-scan-service`/`rendering-service` themselves to check an inbound header from
internal callers - fewer moving parts, same residual race window this ADR already accepted (a
maintenance-mode toggle mid-cascade, narrower than Category B's permanent exposure).
~~**Still open**: `case-service`→`workflow-service`, `migration-service`→`permission-service`/peer
installations/`folder-service`, and `signature-service`→`document-service` — named in this ADR's
"Findings" but not part of the Phase 51 Session 4 plan item, which scoped only the two services above.
Same pattern (`_reject_during_maintenance` reading `X-DMS-Maintenance-Active`) applies directly if a
future session picks these up.~~

**Update, Phase 56 Session 1**: closed all three remaining call sites, exactly the pattern named above —
`case-service`'s `create_case` (guards the `workflow_client.start_instance` cascade), `migration-service`'s
`create_transfer` (guards the scope-lock/peer-installation/eventual-`folder-service`-deletion cascade
across the transfer's own subsequent steps — the step endpoints themselves need no separate check, since
they're already gated to `workflow-service` only, P54-S2/ADR 0173, and letting an already-approved,
already-running transfer's own steps complete during a maintenance toggle matches the same narrow,
accepted residual race window this ADR's own Consequences already named), and `signature-service`'s
`create_signature` (guards the `document_client.checkin_signed_version` cascade — found and corrected a
stale claim in `docs/services/signature-service.md` while closing this: the gateway's own default-deny
already blocks an external caller reaching this endpoint during maintenance, but that check only covers
gateway-*proxied* traffic, not a direct, gateway-bypassing internal network call — ADR 0005's own
"services implicitly trust their internal network position" is exactly the gap a local check closes).
**Category A is now fully closed** for every call site this ADR's own "Findings" section named.
