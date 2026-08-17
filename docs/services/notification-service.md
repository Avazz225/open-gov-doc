# notification-service

**Responsibility:** Notification Service (Concept 7.1) - email, in-app, and webhook notifications. Consumes `workflow.task.escalated` from `workflow-service` (SLA time monitoring, P6-S2) as well as, since P6-S5, `auth.superuser.activated` (break-glass security notification, 4.6), since P6-S6 `permission.maintenance_mode.activated` (emergency shutdown security notification, 4.8), since P6-S9 `workflow.federation.inbound_received` (Federation Hub, 7.4), since **P7-S1** `document.deletion.reminder` (deletion reminder, 5.2a, from `document-service`), since **P7-S1b** additionally `folder.deletion.reminder` (the same deletion reminder for folders, from `folder-service`), since **Post-Roadmap Phase 30 Session 4** additionally `document.lock.reminder` (locked-document reminder, 4.2, from `document-service`), and additionally offers a generic `POST /notifications` that any service can call directly. Since **P6-S6**, this endpoint checks recipient existence against real `auth-service` accounts and remains deliberately reachable even during system-wide maintenance mode (needed for alerting itself) — see [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md). Since **Post-Roadmap Phase 30**, all 10 notification use cases can have their subject/body overridden per use case and/or recipient domain via configurable `EmailTemplate` rows — see [ADR 0111](../adr/0111-configurable-email-templates.md).

**Concept reference:** 7.1, 4.8
**Own Postgres schema:** `notification` (table `notification`)

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/notifications` | Send (`channel`: `email`\|`in_app`\|`webhook`, `recipient`, `subject`, `body`) - persists first, then attempts synchronous delivery. **Always `201`**, the actual result is in the `status` field (`sent`\|`failed`\|`failed_permanent`), no HTTP error code on failed delivery. **Since P6-S6**: for `channel` `email`/`in_app`, `recipient` is first checked against real `auth-service` accounts (`GET /users`) — `400` for an unknown recipient; `webhook` remains unchecked (the target is a URL, not an identity) |
| `GET` | `/notifications?recipient=&channel=&status=` | Filtered list |
| `GET` | `/notifications/{id}` | Detail - `404` |
| `POST` | `/notifications/{id}/retry` | Manual restart of a `failed_permanent` notification (since **P20-S3**, [ADR 0079](../adr/0079-notification-service-retry-backoff-failed-permanent.md)) — `404` for an unknown `id`, `409` if `status != "failed_permanent"`, otherwise an immediate renewed delivery attempt |
| `GET` | `/email-template-use-cases` | Configurable email templates (Post-Roadmap Phase 30, [ADR 0111](../adr/0111-configurable-email-templates.md)) - fixed catalog of the 10 use cases `consumer.py` dispatches on, each with its known `{placeholder}`s |
| `GET` | `/email-templates?use_case=` | List configured `EmailTemplate` rows, optionally filtered |
| `PUT` | `/email-templates/{use_case}` | Upsert the catch-all row (`recipient_domain_pattern IS NULL`) for a use case |
| `PUT` | `/email-templates/{use_case}/by-domain/{domain}` | Upsert a domain-specific override row |
| `DELETE` | `/email-templates/{id}` | Remove a configured row - `404` for an unknown `id`; the affected `(use_case, domain)` falls back to the hardcoded default again |
| `GET` | `/healthz` | Health check |

## Data Model

`notification`: `id`, `channel` (`"email"`\|`"in_app"`\|`"webhook"`), `recipient` (heterogeneous per channel: email address / lane-name-or-user-identifier / webhook URL - deliberately a single generic field instead of three channel-specific columns), `subject`, `body`, `status` (`"sent"`\|`"failed"`\|`"failed_permanent"`, since **P20-S3**), `error` (nullable), `attempts` (integer, default 0, since **P20-S3**), `next_retry_at` (nullable, since **P20-S3**), `created_at`, `sent_at` (nullable).

`email_template` (Post-Roadmap Phase 30, [ADR 0111](../adr/0111-configurable-email-templates.md)): `id`, `use_case` (from a fixed catalog, `templates.EMAIL_TEMPLATE_USE_CASES`), `recipient_domain_pattern` (nullable, `NULL` = catch-all), `subject_template`/`body_template` (`{placeholder}` syntax, `str.format()`-rendered), `updated_at`. `UniqueConstraint(use_case, recipient_domain_pattern)`.

## Channels (`src/notification_service/delivery.py`)

- **Email**: SMTP via `aiosmtplib`, verified against the installed version (5.1.2) via `inspect`. Dev standard points to the `mailpit` container (`infra/docker-compose.yml`, web UI at `MAILPIT_UI_PORT`) - no auth needed. For real SMTP operation, set `smtp_username`/`smtp_password`/`smtp_use_tls`.
- **In-app**: pure persistence, immediately `status="sent"`. No UI this session (querying only via `GET /notifications`) - Concept 7.1 names in-app as a channel, a UI display is not part of P6-S2. Never retry-capable (no real delivery step, cannot fail).
- **Webhook**: HTTP POST via `httpx` to the URL passed as `recipient`, JSON payload `{"subject": ..., "body": ...}`, timeout 5s.

The FIRST delivery attempt still happens **synchronously** when the record is created (fast response in the normal case), the result lands directly on the same record. **Since Post-Roadmap Phase 20 Session 3** ([ADR 0079](../adr/0079-notification-service-retry-backoff-failed-permanent.md)), a failure for `email`/`webhook` is no longer immediately terminal: `status` stays `"failed"` (retry-capable) with increasing `attempts` and a `next_retry_at` set via full-jitter backoff (`libs/dms-retry`), which a new, standalone `_notification_retry_poll_loop` (interval `notification_retry_poll_interval_seconds`, default 60s) works through — delivery itself remains synchronous/inline, only the RETRY runs asynchronously, so as not to block the NATS handler. Only after `max_notification_attempts` (default 5) unsuccessful attempts does `status` switch to the real terminal status `failed_permanent`, from which `POST /notifications/{id}/retry` allows an immediate manual restart.

## `workflow.task.escalated` consumer (`src/notification_service/consumer.py`)

Subscribes **specifically** to `workflow.task.escalated` (not `workflow.>` - `workflow.instance.*`/`workflow.task.completed` have no notification semantics). Per event:

1. **Always** creates an in-app notification (`recipient` = `lane` value from the event payload, otherwise `"unassigned"` - no role resolution without RBAC, see "Open Points").
2. **Additionally** creates an email notification if the payload contains an `escalation_email` value (an opaque, unvalidated process datum from `initial_data` at instance start, convention like `business_key` in `workflow-service`).

After each delivery, `notification.sent`/`notification.failed` is published.

## `auth.superuser.activated` consumer (since P6-S5, 4.6)

Second branch of the same `consumer.py` handler, dispatching on `event.event_type` instead of on payload fields (unlike the SLA branch, which needs no own `event_type` comparison since only one subject was consumed so far). Creates a **single** email notification to `settings.security_officer_email` (fixed configuration, no recipient resolution mechanism needed as with `escalation_email`) — implementation of the security notification described in 4.6 as "optional" on break-glass activation.

## `permission.maintenance_mode.activated` consumer (since P6-S6, 4.8)

Third branch of the same `consumer.py` handler, identical dispatch principle to the break-glass branch — creates a single email notification to `settings.security_officer_email` ("system-wide emergency lock triggered"). This consumer continues to run unchanged **even while system-wide maintenance mode itself is active** — the service is deliberately not part of the gateway blockade for this reason (see below), otherwise it could not deliver exactly the alerting that 4.8 requires for its own activation.

## `workflow.federation.inbound_received` consumer (since P6-S9, 7.4)

Fourth branch of the same `consumer.py` handler — notification of the target installation on an incoming federated handover via the Federation Hub (see `docs/services/workflow-service.md` "Federation"). Same `notify_email` pattern as the SLA branch: always an in-app notification (recipient `"unassigned"`, since there is no lane name for a process freshly started from outside), plus an email if the sending side supplied a `notify_email` process datum.

## `document.deletion.reminder` consumer (since P7-S1, 5.2a)

Fifth branch of the same `consumer.py` handler, own stream subject (`"document"`, see `settings.subjects`) — no collision risk with the previous `workflow.>` subjects, since this is its own stream. Same `notify_email` pattern as the federation branch: **always** an in-app notification (recipient `"unassigned"` — no role/user resolution, the same deliberate simplification pattern as everywhere else in this service), plus an email if the event contains an optional `notify_email` field (`Document.reminder_notify_email`, supplied by the caller when setting the retention, otherwise no email). Triggered by `document-service`'s `_retention_poll_loop` once a document is due for deletion within the configured lead time (`RetentionConfig.reminder_lead_days`) (see `docs/services/document-service.md` "Retention & Legal Hold").

**Real bug found and fixed**: `workflow.federation.inbound_received` shares the `"workflow"` stream with the already existing `workflow.task.escalated` (P6-S2). A durable JetStream consumer name is unique per **stream**, not per subject — a second `subscribe()` call with the same durable name `"notification-service"` but a different filter subject on the same stream fails with `"consumer is already bound to a subscription"` (reproducible on every restart/test run). Fix in `start_consuming()`: the new subject gets its own, second durable name (`"notification-service-federation"`) — the three already existing subjects keep their original name (no redelivery of their previous history).

## `folder.deletion.reminder` consumer (since P7-S1b, 5.2a)

Sixth branch of the same `consumer.py` handler, 1:1 the same pattern as `document.deletion.reminder` (see above), just `folder-service`'s `name` field instead of `title` in the payload. Own, previously unconsumed `"folder"` stream subject by this service — since this is the **first** subject of this service on this stream, (unlike `workflow.federation.inbound_received`) **no** second durable name was needed; the bug described above did not occur here in the first place.

## `license.*` consumers (since P9-S1, 9.2)

Three further branches of the same `consumer.py` handler (`license.limit_exceeded`/`license.expiring_soon`/`license.invalid`), 1:1 the same pattern as `_handle_maintenance_mode_activated` — fixed `settings.license_admin_email` address, no recipient resolution mechanism. Since all three subjects share the new `"license"` stream (unlike `document.deletion.reminder`/`folder.deletion.reminder`, which were each the **first** subject of their stream), **each** needed its own durable name (`notification-service-license-limit-exceeded`/`-expiring-soon`/`-invalid`) — the same durable name for multiple filter subjects on the same stream fails with "consumer is already bound to a subscription," the same restriction that had already required a second durable name for `workflow.federation.inbound_received`.

## Recipient existence check (`POST /notifications`, since P6-S6, 4.8 retrofit)

New, thin `auth_client.py`: `recipient_exists(recipient, channel)` — for `channel="webhook"` always `True` (the target is a URL, not an identity), otherwise `GET /users` at the Auth Service and comparison against `username`/`email`. Since `GET /users` has been gated behind the capability `admin.user_management` since P6-S5, `notification-service` authenticates for this as the existing technical account `users-admin` (`POST /login` on **every** call, no token caching — an accepted latency trade-off for a low-frequency internal check, no third technical account introduced). `POST /notifications` rejects an unknown recipient for `email`/`in_app` with `400` **before** `repository.create_and_send` is called — the `workflow.task.escalated` consumer and the two security notification consumers (`auth.superuser.activated`, `permission.maintenance_mode.activated`) continue to call `repository.create_and_send` directly, not via this HTTP endpoint, and are therefore unaffected by the check (see "Recipient resolution" in Open Points).

## Reachability during maintenance mode (4.8, since P6-S6)

`notification-service` is **deliberately not** in the gateway allow-list for maintenance mode, but is also not affected by it: the gateway lock only blocks *proxied* requests to backends from outside; `notification-service` itself receives its emergency shutdown alert via NATS (see above), not via a proxied HTTP call. `POST /notifications` called directly on the gateway would be blocked like any other endpoint during an active lock — only the internal event consumption remains functional in every case. See [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md) for the full rationale.

## Public frontend base URLs & direct links (Post-Roadmap Phase 27, ADR 0105)

Three new nullable settings — `user_ui_public_base_url`, `reviewer_ui_public_base_url`,
`admin_ui_public_base_url` (`DMS_USER_UI_PUBLIC_BASE_URL` etc.) — hold the
browser-reachable base URL of each frontend app, mirroring auth-service's
`keycloak_public_base_url` (ADR 0062). `notification_service/links.py`'s
`build_resource_link(base_url, resource_type, resource_id)` builds a direct
link into a resource per Phase 29's URL scheme (`?document=`/`?folder=`/
`?instance=`), returning `None` (link-building skipped) when the relevant
base URL isn't configured.

**Wired into three handlers (Post-Roadmap Phase 29 Session 5, [ADR 0109](../adr/0109-direct-link-url-scheme.md))** — the ones that already had a concrete, addressable resource reference at hand: `_handle_task_escalated` (`event.subject` = instance ID → `reviewer_ui_public_base_url`), `_handle_deletion_reminder` (`event.subject` = document ID → `user_ui_public_base_url`), `_handle_folder_deletion_reminder` (`event.subject` = folder ID → `user_ui_public_base_url`). Each appends `\n\n<Label>: <link>` to the email body only when the relevant base URL is configured — unconfigured installations see the exact same body as before this session. The remaining handlers (break-glass, license, maintenance mode) have no single addressable DMS resource to link to and were left unchanged. Phase 30's `{link}` email template placeholder still reuses `build_resource_link()` directly.

## `document.lock.reminder` consumer (Post-Roadmap Phase 30 Session 4, 4.2, [ADR 0111](../adr/0111-configurable-email-templates.md))

Tenth branch of the same `consumer.py` handler, second subject on the `"document"` stream (own durable name `notification-service-lock-reminder`, same reasoning as `workflow.federation.inbound_received`). Triggered by `document-service`'s new `_lock_reminder_poll_loop` once a `DocumentLock` has sat unrenewed longer than `lock_reminder_threshold_seconds` (default 4h). Unlike the deletion reminders, there is no separate `notify_email` concept here — the lock's own `locked_by` (the person who most plausibly forgot about it) is directly the in-app recipient, and (when it happens to resolve to a real email address via a configured `EmailTemplate`'s domain matching) the email target too. Payload: `{title, locked_by}` (document ID is `event.subject`).

## Configurable email templates (Post-Roadmap Phase 30, [ADR 0111](../adr/0111-configurable-email-templates.md))

`src/notification_service/templates.py` adds `EmailTemplate`-backed subject/body overrides for **all 10** `consumer.py` use cases (the 9 pre-existing handlers plus the new lock reminder above). `resolve_template(session, use_case, recipient)` tries an exact `(use_case, domain-of-recipient)` row, then the `(use_case, NULL)` catch-all row, then returns `None`. `consumer.py`'s new `_render_or_fallback()` helper wraps every handler's notification-send call: `None` → the handler's original hardcoded subject/body, unchanged from before this feature (guaranteeing zero behavior change for any installation that has never configured a template); a resolved template's `render_template()` (`str.format()`-based) either substitutes cleanly or raises `UnknownPlaceholderError` on a typo'd placeholder, in which case the fallback is used and a warning logged — a misconfigured template can never cause a broken/half-rendered email to actually be sent. Resolution runs **per notification, not per event**: an event producing both an in-app and an email notification (e.g. `workflow.task.escalated`) resolves separately per channel's own recipient, so a domain-specific override can legitimately change only the email wording, not the in-app one (which almost never carries an `"@"` recipient and therefore only ever matches a catch-all row). `GET /email-template-use-cases` exposes the fixed catalog (with each use case's known placeholders) for the admin-ui form (`apps/admin-ui/src/app/email-templates/`, `EmailTemplates.tsx`) - deliberately a closed catalog rather than free text, since it mirrors `consumer.py`'s actual fixed set of dispatch branches (see ADR 0111 for the full rationale against `ApprovalActionConfig`'s open free-text `action_type`).

## Events

**Published** (stream `notification`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `notification.sent` | `{channel, recipient}` |
| `notification.failed` | `{channel, recipient, error}` |

**Consumed** (`durable="notification-service"`, except where noted): `workflow.task.escalated` (from `workflow-service`), since P6-S5 additionally `auth.superuser.activated` (from `auth-service`), since P6-S6 additionally `permission.maintenance_mode.activated` (from `permission-service`, see `docs/services/permission-service.md`), since P6-S9 additionally `workflow.federation.inbound_received` (from `workflow-service`, see `docs/services/workflow-service.md` "Federation"), since **P7-S1** additionally `document.deletion.reminder` (from `document-service`, own `"document"` stream subject, see above), since **P7-S1b** additionally `folder.deletion.reminder` (from `folder-service`, own `"folder"` stream subject, see above), since **P9-S1** additionally `license.limit_exceeded`/`license.expiring_soon`/`license.invalid` (from `license-service`, each with its own durable name `notification-service-license-*`, see above), since **Post-Roadmap Phase 30 Session 4** additionally `document.lock.reminder` (from `document-service`, second `"document"` stream subject, own durable name `notification-service-lock-reminder`, see above).

## Self-registration (Concept 3.2a, since P4-S1)

Registers itself with the registry on startup, identical pattern to every other service.

## Sensors (Concept 10.1)

None yet - follows in Phase 11.

## Tests

`uv run pytest services/notification-service/tests` (**40 tests**, of which 10 are new since **Post-Roadmap Phase 20 Session 3**, [ADR 0079](../adr/0079-notification-service-retry-backoff-failed-permanent.md)) - runs against a real Postgres instance, real NATS, and (for the email paths) against the real `mailpit` container, no mocking:
- `test_delivery.py` - email real against `mailpit`, webhook against an `http.server` started locally within the test suite, each with success and failure cases (unreachable SMTP server/unreachable URL).
- `test_repository.py` - `create_and_send` incl. persistence of the failure case, filtering by `recipient`/`channel`. Since P20-S3 additionally: backoff behavior below/at exhaustion of `max_notification_attempts`, `list_due_for_retry` filtering (status AND backoff window), `retry_now`.
- `test_api.py` - all endpoints incl. `404`. Since P20-S3 additionally: new `/retry` endpoint incl. `404`/`409`/successful restart.
- `test_main.py` (new since P20-S3) - `_run_retry_tick` picks up a due notification and attempts redelivery, skips one not yet due.
- `test_consumer.py` - simulated `workflow.task.escalated` event (directly against `consumer.make_handler`, without real NATS) produces the expected in-app/email notifications, incl. the case without `escalation_email` (in-app only); since **P6-S6** additionally a simulated `permission.maintenance_mode.activated` event produces the security notification to `security_officer_email`; since **P6-S9** additionally a simulated `workflow.federation.inbound_received` event with/without `notify_email` (in-app+email or in-app only, same pattern as the SLA branch).
- Since **P6-S6** additionally: `test_api.py` uses a new `real_recipient` fixture (creates a real user via the live running `auth-service`, `users-admin` login) for the success cases, plus own tests for `400` on an unknown recipient (`email`/`in_app`) — no mocking of `auth-service`.
- Since **P7-S1** additionally: `test_consumer.py` simulated `document.deletion.reminder` event with/without `notify_email` (in-app+email or in-app only).
- Since **P7-S1b** additionally: `test_consumer.py` simulated `folder.deletion.reminder` event with `notify_email` (in-app+email).
- Since **Post-Roadmap Phase 27 Session 1** additionally: `test_links.py` (new, 6 tests) - `build_resource_link` per resource type, trailing-slash normalization, `None` base URL, resource-ID URL-encoding (see ADR 0105).
- Since **Post-Roadmap Phase 29 Session 5** additionally: `test_consumer.py` - direct-link presence/absence in the email body for `workflow.task.escalated`/`document.deletion.reminder`/`folder.deletion.reminder`, with and without the relevant public base URL configured (ADR 0109).
- Since **Post-Roadmap Phase 30** additionally: `test_templates.py` (new, 8 tests) - `render_template`/`template_placeholders`, and `resolve_template`'s three-tier resolution (exact domain > catch-all > `None`), including a non-email recipient matching only a catch-all row, never a domain-specific one. `test_repository.py` additionally covers the `EmailTemplate` CRUD functions (upsert-creates-then-updates-in-place, domain-specific as a separate row from catch-all, filter by `use_case`, delete, `NotFoundError`). `test_consumer.py` additionally covers, for **every one of the now 10 use cases**: unchanged fallback behavior with no configured row (proving zero behavior change), a configured catch-all template actually being rendered, domain-specific overriding catch-all for `document.deletion.reminder` specifically (and the in-app channel's "unassigned" recipient still only matching the catch-all, never the domain row), a misconfigured template with an unknown placeholder falling back rather than sending broken text, and the new `document.lock.reminder` handler's in-app-to-the-lock-holder behavior with/without a direct link and with/without a configured template (see ADR 0111).
- **78 tests** (previously 50).
- Pure backend session, no browser test needed (the corresponding admin-ui page has its own vitest suite, see `apps/admin-ui/tests/email-templates.test.tsx`, plus a live Playwright round-trip in `apps/admin-ui/e2e/email-templates.spec.ts`).

## Open Points

- **Role check since P6-S6 only as a recipient existence check** — `POST /notifications` still does not require any permission of the *caller*, only that the specified `recipient` (for `email`/`in_app`) is a real `auth-service` account. Any authenticated principal can still trigger a notification for any known recipient — no permission check of the caller itself, see [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md) (user decision, narrower retrofit scope).
- **No recipient resolution via roles** - a BPMN lane is used only informatively as `recipient` for in-app notifications, not resolved against real user accounts/roles in `auth-service` (no role query for users currently exists there either). A "supervisor role" could in the future be mapped to real email addresses instead of relying on the opaque `escalation_email` process datum — still not part of this session.
- **Technical account `users-admin` has also served as internal service login since P6-S6** — `notification-service` authenticates as a foreign technical account for the recipient check instead of its own identity; a recurrence of this pattern with further growth should be revisited (see ADR 0024 "Consequences").
- ~~**No retry/no dead-letter handling**~~ — **fixed in Post-Roadmap Phase 20 Session 3** ([ADR 0079](../adr/0079-notification-service-retry-backoff-failed-permanent.md)): automatic retry with full-jitter backoff up to `max_notification_attempts`, then `failed_permanent` + manual restart via `POST .../retry`. ~~Still open: an Admin UI visibility/control for this~~ — **fixed in Post-Roadmap Phase 20 Session 7** ([ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)): `ProcessingFailuresView` (`/processing-failures/`) in `admin-ui`.
- **No retrofit of existing "logs only" alerting points** - `storage-service` (and others mentioned at various points in the concept as future consumers, such as force-unlock, license expiry, report dispatch, monitoring escalation) remain **not** hooked up to the service. Break-glass (4.6, since P6-S5), **deletion-period advance notice (5.2a, since P7-S1)**, and **the locked-document reminder (4.2, since Post-Roadmap Phase 30 Session 4)** are the exceptions so far (see above). Each future consumer registers its own subject in `settings.py`'s `subjects` list once it is actually connected.
- **One notification record per channel, no multi-channel fan-out from a single call** - anyone wanting to distribute an escalation across multiple channels simultaneously (e.g. email and webhook) must call `POST /notifications` multiple times. The `workflow.task.escalated` consumer itself covers exactly the case described in Concept 7.1 (always in-app, optionally additionally email).
- **No rate limiting/spam protection** - a process with a very short, repeatedly firing cycle timer (not tested this session, see `docs/services/workflow-service.md` "Open Points") could repeatedly notify the same recipient.
