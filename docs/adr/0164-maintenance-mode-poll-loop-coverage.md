# 0164 — Rolling out `is_maintenance_active()` to background poll loops (ADR 0152's recommendation)

**Status:** accepted
**Context:** P44-S3 (Phase 44, "Security & Correctness Hardening"), second half of the session bundled
with (a) the maintenance-mode coverage gap 4.8/ADR 0024 itself named for `federation-hub-service`/
`plugin-orchestration-service` (both now exist, unlike when ADR 0024 was written), and (b) the
service-to-service write-enforcement build [ADR 0152](0152-maintenance-mode-vs-direct-service-writes-scoping.md)
already scoped. This ADR covers (b) in full plus the poll-loop half of (a) — ADR 0152 already had its
design decided (extend, don't re-decide, per this session's own Definition of Done); this ADR just
records the rollout and the two genuinely new decisions it forced: `federation-hub-service`'s optional
client, and `plugin-orchestration-service`'s "halt instances" analog.

## Decision

**Extended `libs/dms-permission-client` with `is_maintenance_active()`** (`GET /maintenance-mode`, no
caching — mirrors `workflow-service`'s own already-working local `permission_client.py` method almost
verbatim, exactly as ADR 0152 recommended).

**Rolled out a maintenance-mode skip to every Category B poll loop ADR 0152 identified, plus two more
found while rolling it out** (the plan's own shorthand named "~9 call sites across 8 services" but
undercounted by two):

| Service | Loop(s) | Client |
|---|---|---|
| `archival-service` | `_archival_poll_loop` | shared lib |
| `folder-service` | `_retention_poll_loop` | shared lib |
| `document-service` | `_retention_poll_loop`, `_lock_reminder_poll_loop`, **`_folder_export_poll_loop`** (not named by ADR 0152 — found during rollout, identical Category B shape) | local `permission_client.py`, extended |
| `mail-connector` | `_poll_loop` | local `permission_client.py`, extended |
| `ocr-service` | `_ocr_retry_poll_loop` | shared lib |
| `rendering-service` | `_rendition_retry_poll_loop` | shared lib |
| `reporting-service` | `_report_schedule_poll_loop` | shared lib |
| `federation-hub-service` | `_handover_retry_poll_loop` | **new**, optional (see below) |
| `plugin-orchestration-service` | `create_placement` (not a poll loop — see below) | local `PermissionServiceClient`, extended |

Each loop skips its entire tick (`await asyncio.sleep(interval); continue`) when the check reports
maintenance mode active, exactly `workflow-service`'s own `_sla_poll_loop` precedent.

**`federation-hub-service` gets a NEW, deliberately OPTIONAL `permission_client`** (`Settings.
permission_service_base_url: str | None = None`, unset by default). This service is NOT scoped to one
installation (its own settings docstring: "other installations' hubs/admins reach it directly too") —
there is no single, universally-correct `permission-service` to query. A hub operator running this
service for exactly one installation (this project's own dev stack, and a plausible shape for a small
real deployment) may configure it so that installation's own lockdown also pauses this hub's retry
activity; a hub genuinely serving multiple installations should leave it unset, since one installation's
lockdown pausing delivery for every OTHER installation too would be a regression, not a safety feature.
The dev stack's `infra/docker-compose.yml` now sets it (self-loopback, same "dev-only convenience" shape
already established for this service, see its own settings docstring) — this is the ONE opt-in
configuration this ADR actually enables end-to-end.

**`plugin-orchestration-service` gets a maintenance-mode check on `POST /placements`, not a poll loop
fix** — this service has no poll loop and no real container automation (recommendation-only, per its own
docs' Open Points); it never actually starts or stops anything. The closest honest analog to 4.8's "halt
plugin instances" it can build is refusing to authorize a NEW placement decision during a lockdown,
mirroring `workflow-service`'s own identical treatment of new process-instance starts. `503` while active.

## Rationale

- **Why extend the shared lib rather than only patching local clients**: five of the nine call sites
  already used `dms_permission_client.PermissionServiceClient` — extending it once covers five services
  in one change, consistent with the library's own stated purpose (deduplicating this exact class).
  `document-service`/`mail-connector`/`plugin-orchestration-service` keep their own local copies for the
  same reasons those copies already existed independently (documented at each site, not revisited here).
- **Why `federation-hub-service`'s client is optional instead of a required config value or a hardcoded
  "ask the local installation's hub" assumption**: the hub's entire design point (ADR 0028/0039) is that
  it is shared, cross-installation infrastructure with no privileged relationship to any one
  installation's permission-service. Making the maintenance check unconditional would either force every
  hub deployment to name ONE installation's permission-service as authoritative (wrong for a real
  multi-installation hub) or silently do nothing useful (wrong for the common single-installation dev/
  small-deployment shape this project's own compose file already models). Optional, explicit opt-in
  respects both shapes honestly instead of picking one as a hidden default.
- **Why `plugin-orchestration-service` gets an endpoint check instead of a poll-loop fix**: there IS no
  poll loop to fix - the service's own `psutil`-based node sampler and `resource-usage` self-reporting
  are pure telemetry, not writes an emergency lockdown needs to stop. `POST /placements` is the one
  mutating, "start something new" action this service has, and it already goes through a real
  permission check (`admin.orchestration`) - adding the maintenance check right next to it costs nothing
  architecturally and is the truthful mapping of 4.8's intent onto what this service can actually do.
- **Why `document-service`'s `_folder_export_poll_loop` needed the same fix even though ADR 0152 didn't
  name it**: it has the exact same shape as every other Category B loop (background timer, no gateway
  involvement, real writes — storage upload, rendering calls, `document.exported` events) - ADR 0152's
  own enumeration was a research-time inventory, not an exhaustive guarantee, and leaving a
  structurally-identical loop unfixed just because it wasn't named would defeat the point of the rollout.
- **Why a lockdown pauses a document-lock reminder notification, not just destructive writes**: ADR 0152
  itself didn't distinguish severity within Category B, and this project's own precedent
  (`_sla_poll_loop`'s skip) already applies uniformly to its own loop regardless of what each tick would
  have done - consistency with the existing pattern was judged more valuable than a bespoke severity
  tier this session didn't otherwise need.
- **Why Category A (request-triggered cascades) is still not addressed**: ADR 0152 named it the
  lower-priority half (the inbound request was already checked once) and this session's own plan item
  only asked for the write-enforcement *build*, which ADR 0152's own text frames as "Category B's
  poll-loop writes... first" — Category A's header-forwarding variant remains a distinct, still-open
  follow-up, not silently expanded into this session's scope.

## Consequences

- **`federation-hub-service` has its first-ever `permission-service` dependency** — a new
  `dms-permission-client` package dependency, a new optional settings field, and (in the dev stack only)
  a new `depends_on: permission-service` in `infra/docker-compose.yml`. Any other environment running
  this service standalone (the documented production shape) is unaffected unless it deliberately opts in.
- **Regression tests**: a new unit test pair for `is_maintenance_active()` in `libs/dms-permission-
  client`'s existing `MockTransport`-based suite (16/16 passing, was 14); a new live-task test in
  `federation-hub-service` proving the guard actually prevents redelivery while active (the only one of
  the nine rollout sites with a dedicated test of this shape — the other eight share the identical,
  already-proven one-line guard and are covered by their full existing suites still passing unmodified,
  the same proportionality judgment [ADR 0163](0163-teamspace-folder-delete-permission-and-orphan-resource-cleanup.md)
  made for its own fourth call site); a new test for `plugin-orchestration-service`'s `POST /placements`
  `503` rejection (its existing `client` fixture's `permission_client` `AsyncMock` needed an explicit
  `is_maintenance_active.return_value = False` default added, or every pre-existing placement test would
  have broken against the mock's default truthy return).
- **No code changes needed in `permission-service` itself** — `GET /maintenance-mode`'s contract was
  already stable and already well-tested from ADR 0024's original session; this ADR only adds new
  callers of an existing endpoint.
- ~~**Category A (request-triggered cascading writes) remains explicitly unaddressed** — ADR 0152's own
  documented, lower-priority second half, unchanged by this session.~~ — **closed in two rounds**: Phase
  51 Session 4 (see ADR 0152's own "Update" note) closed `document-service`/`folder-service`'s cascading
  endpoints (reject during maintenance mode via the gateway-forwarded header, the same shape
  `workflow-service` already had); this addendum's own "remain open" claim about
  `case-service`/`migration-service`/`signature-service` was itself superseded by ADR 0152's later
  "Update, Phase 56 Session 1" note, which confirms all three closed (`case-service.create_case`,
  `migration-service.create_transfer`, `signature-service.create_signature`, each via
  `_reject_during_maintenance`) — found during Phase 65+'s gap-analysis round, this addendum had simply
  never been updated after that second closure round.
