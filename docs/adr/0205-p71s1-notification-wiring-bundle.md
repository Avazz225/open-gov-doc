# 0205 — P71-S1: Notification Wiring Bundle

**Status:** accepted
**Context:** P71-S1 (Phase 71, first session, ninth gap-analysis round). The plan's own framing: "each
just needs a `subjects` entry plus a producer-side `publish_event` call, same pattern as the existing
four" for four targets (storage-service alerts, force-unlock, report dispatch, monitoring escalation),
bundled with `permission-service`'s missing approver-notification/execution-feedback channel. Verified
against the real code before building — three of the five sub-items matched the plan's premise exactly,
one needed a different fix than described, and one genuinely doesn't exist as a hookable target today.

## What was built

- **Force-unlock** (`document.lock.force_released`/`document.force_unlock.failed`): exactly as the plan
  described — both events already existed unconditionally (P6-S4/P66-S3), only `notification-service`'s
  consumer side was missing. Two new branches in `consumer.py`, two new `subjects` entries (third/fourth
  on the existing `"document"` stream). Closes the gap ADR 0022's own "Consequences" section named
  verbatim ("no execution feedback channel... a failed force-unlock is only logged locally").
- **Four-eyes approval lifecycle** (`permission.approval.approved`/`.rejected`): also exactly as the plan
  described, just not literally what its text said — `permission-service` already publishes both events
  unconditionally; this closes the "approver-notification/execution-feedback channel" gap by wiring
  `notification-service` to actually consume them, not by adding anything to
  `permission-service`/`approval_consumer.py` itself (which the plan's phrasing implied would need
  changes; it needed none). Recipient = `initiated_by`, in-app.
- **Storage-service alerts**: the plan undersold this one — storage-service had **no event bus
  connection of any kind** before this session (confirmed by grep: zero `NatsEventBusClient` usage
  anywhere), not "just needs a subjects entry." Added a first-ever `NatsEventBusClient` producer (new
  `"storage"` stream) to `main.py`'s lifespan, plus `publish_event` threaded as an optional parameter
  into `replication.process_pending` (permanent replication failure) and `replication.verify_pending`
  (fixity mismatch) — optional so every existing call site/test that doesn't care about alerting needed
  no changes. `notification-service` gained two new consumers, both emailing a fixed
  `storage_admin_email` (neither payload carries a principal identity — an object key/backend id, not a
  person).
- **Report dispatch**: the plan's framing ("same pattern as the other [notification-service subjects]")
  didn't hold at all — `reporting-service` doesn't use the NATS pattern for this; it already calls
  `notification-service`'s HTTP `POST /notifications` directly on success. The actual gap was a missing
  **failure**-path notification in `reporting-service`'s own poll tick (`_run_due_schedules`), not a
  notification-service wiring task. Fixed there instead: the report-generation-failure branch now also
  attempts a best-effort email to `schedule.recipient_email`, in its own nested `try`/`except`. The
  email-*delivery*-failure branch deliberately was NOT given a second, redundant send attempt (retrying
  the exact channel that just failed is circular, and `NotificationClient` has no other channel) — an
  explicit, accepted residual gap for that one narrow failure mode, not silently left unaddressed.

## What was deliberately NOT built: monitoring escalation

Verified before attempting: `monitoring-service` publishes exactly one event
(`monitoring.sensor.config_changed`, an admin toggling a sensor on/off) — no threshold/escalation
concept of any kind exists anywhere in the service, and `docs/services/monitoring-service.md` has no
"Open Points" mention of one either. The plan's premise ("each just needs a subjects entry plus a
producer-side publish_event call") assumes an existing threshold check this service simply doesn't have.
Building one would mean designing what threshold, computed from what scraped metric, crosses what
condition — a real scoping decision, not a mechanical wiring task like the other four. Deliberately left
unbuilt rather than force-fitting a fake threshold just to have something to wire; documented as a
genuine open point in `docs/services/notification-service.md`, to be picked up as its own session if an
operator need for it is ever identified.

## Verification

`notification-service` 106/106 (+6: two force-unlock, two approval-lifecycle, two storage-alerting
consumer tests). `reporting-service` 81/81 (+2: generation-failure-notifies-recipient, and
generation-failure-survives-notification-also-failing). `storage-service` 169/169 (+2: publish_event
called with the correct payload on permanent replication failure and on a fixity mismatch). All three
services rebuilt, redeployed, and live-verified against the real running stack — including a real,
end-to-end round trip for the genuinely new piece: a real object uploaded to `storage-service`, its
on-disk content corrupted directly, a full fixity sweep run against all 13,556 real objects in the dev
stack's own data (found exactly the 1 real mismatch), and the resulting `storage.object_verify.mismatch`
event confirmed to have produced a real email notification row in `notification-service`'s own database,
addressed to `storage_admin_email`, with the correct object key and backend name in the body. The test
object's content was restored afterward.

**Incidental deployment bug caught by this live verification, fixed in the same session**:
`storage-service`'s `pyproject.toml` was missing the new `dms-eventbus-client` dependency entirely — the
local `uv run pytest`/`ruff` runs never caught this because this repo's uv workspace shares one lockfile/
venv across all services, so the module was already importable there regardless of what any individual
service's `pyproject.toml` declared. The Docker image build (an isolated `uv sync` per service) is the
only place this gap was ever going to surface, and it did: the container crashed on startup with
`ModuleNotFoundError: No module named 'dms_eventbus_client'`. Fixed by adding the dependency entry; a
reminder that a clean local test run does not guarantee a clean container build in this workspace layout.

## Consequences

- `storage-service` now has its own event bus connection for the first time — a real, if narrow,
  architectural addition (previously a purely request/response service with no producer/consumer role at
  all).
- The "logs instead of alerted" pattern this bundle closed for storage-service/force-unlock/approval-
  lifecycle is now the established shape for any future alerting-worthy failure in this codebase: a
  dedicated event, a `notification-service` consumer, and (when no principal identity exists in the
  payload) a fixed admin-address setting following the `security_officer_email`/`license_admin_email`
  precedent.
- Monitoring escalation and the `virus_scan.completed` use-case's missing `EmailTemplate` registration
  (found in passing, not part of this bundle's scope) remain open, both explicitly documented rather than
  silently dropped.
