# 0111 — Configurable email templates: `ApprovalActionConfig` shape + `kennzeichen_format` rendering, fixed use-case catalog

**Status:** accepted (P30-S1–S5, see `IMPLEMENTATION_PLAN.md`)
**Context:** new post-roadmap feature (configurable email content), affects `services/notification-service/`, `services/document-service/`, `apps/admin-ui/`

## Decision

A new `EmailTemplate` model in `notification-service` lets an installation
override the subject/body of any notification `consumer.py` sends, keyed by
`(use_case, recipient_domain_pattern)`. `use_case` is a **fixed, closed
catalog** (`EMAIL_TEMPLATE_USE_CASES` in `templates.py`) matching
`consumer.py`'s dispatch branches one-to-one, not open free text.
`recipient_domain_pattern IS NULL` is the catch-all row for a use case
(any recipient); a non-`NULL` value narrows to exactly that domain.
`subject_template`/`body_template` use `{placeholder}` syntax, rendered via
Python's `str.format()`. Resolution at send time
(`templates.resolve_template`): an exact `(use_case, domain)` row wins over
the `(use_case, NULL)` catch-all, which wins over `None` — and `None` means
each of the 9 (now 10, see below) existing hardcoded handlers in
`consumer.py` keeps its unmodified f-string exactly as before this feature
existed.

## Rationale

- **`ApprovalActionConfig`'s keyed-discriminator + no-row-fallback shape,
  reused verbatim**: `permission-service`'s `ApprovalActionConfig`
  (`models.py:78–94`) already established "a row keyed by a discriminator
  string, absence of a row means an implicit default applies" for a
  different four-eyes-principle feature (already reused once for
  `ExportConfig`/`FolderExportJob` in [ADR 0107](0107-pdf-export-two-pass-merge-subnumbering.md)).
  A third feature reusing the same shape keeps the codebase's configuration
  surfaces predictable rather than inventing a fourth variant.
- **`kennzeichen_format`'s `{placeholder}`/`str.format()` convention,
  reused verbatim**: `object-type-service`'s `_render_kennzeichen`/
  `_validate_kennzeichen_format` already established this exact templating
  mechanism for a different configurable-text feature. `render_template`
  deliberately fails loudly (`UnknownPlaceholderError` on a `KeyError`)
  rather than emitting a literal unfilled `{typo}` in a sent email — the
  caller (`consumer.py`'s `_render_or_fallback`) catches it and falls back
  to the hardcoded default instead, logging a warning.
- **Fixed catalog, unlike `ApprovalActionConfig`'s open free text**: this is
  the one deliberate deviation. `ApprovalActionConfig.action_type` accepts
  any string because its callers are an open-ended, growing set
  (`document.force_unlock`, `auth.superuser.activate`, ...) each choosing
  their own name independently. `consumer.py`'s handlers are the opposite:
  a fixed, closed set of `if event.event_type == "..."` branches in one
  file. A closed catalog endpoint (`GET /email-template-use-cases`, listing
  exactly these 10 use cases with their known placeholders) is strictly
  more useful for the admin-ui form than accepting arbitrary text nobody
  can look up, and it can never drift from what `consumer.py` actually
  dispatches on.
- **Per-notification resolution, not per-event**: an event that produces
  both an in-app and an email notification (e.g. `workflow.task.escalated`)
  resolves the template **separately for each channel's own recipient** —
  the in-app recipient (a lane name / `"unassigned"`, never containing
  `"@"`) can only ever match a catch-all row, while the email recipient can
  match a domain-specific override. This means a domain-specific template
  can legitimately produce different wording for the email than the in-app
  notification receives for the same event — an intentional consequence of
  resolving per actual send, not a shared decision made once per event.
- **`document.lock.reminder` as the first genuinely new use case
  (P30-S4)**: `document-service`'s editing lock (4.2,
  [ADR 0002](0002-document-locking-optimistic-conflict-detection.md)) had
  no notification hook at all before this session — the new
  `_lock_reminder_poll_loop` (mirroring `_retention_poll_loop`'s idiom
  exactly) is the first background sweep that feature has ever had. Unlike
  the deletion reminders (which have no natural "owner" and fall back to
  `"unassigned"` for the in-app channel), a lock always has a known holder
  (`DocumentLock.locked_by`), so the in-app recipient is the holder
  directly — plausibly the person who forgot to release it. Dedup follows
  the exact same "sent once per current deadline, reset on change"
  shape as `Document.deletion_reminder_sent_at`: `DocumentLock.
  reminder_sent_at` is reset to `NULL` on every `acquire_lock` call, since
  a fresh acquisition and a same-holder renewal both restart `locked_at`
  (the "how long has this been sitting locked" clock).

## Consequences

- Ten use cases exist at the catalog's introduction: the 9 handlers
  migrated in P30-S3 (`workflow.task.escalated`,
  `workflow.federation.inbound_received`, `document.deletion.reminder`,
  `folder.deletion.reminder`, `auth.superuser.activated`,
  `permission.maintenance_mode.activated`, `license.limit_exceeded`,
  `license.expiring_soon`, `license.invalid`) plus the new
  `document.lock.reminder` from P30-S4. Adding an 11th requires both a new
  `consumer.py` branch and a new `EMAIL_TEMPLATE_USE_CASES` entry — the two
  are intentionally coupled, not independently extensible from the admin-ui
  side.
- The `/email-templates*` endpoints are deliberately ungated, mirroring
  `ApprovalActionConfig`'s own precedent
  ([ADR 0089](0089-approval-settings-ui-config-endpoint-stays-ungated.md)): a
  feature-specific RBAC gate here would be inconsistent with every other
  still-ungated configuration write endpoint in the project and is better
  addressed as a system-wide retrofit (Phase 19's still-open items), not
  per-feature.
- A misconfigured template (a typo'd placeholder) degrades to the
  hardcoded default rather than ever sending broken text — verified by a
  dedicated regression test
  (`test_deletion_reminder_falls_back_when_template_has_unknown_placeholder`).
- `lock_reminder_threshold_seconds` (default 4 hours) deliberately sits far
  above `default_lock_timeout_seconds` (30 minutes, unrelated existing
  setting): a normal, actively renewed edit session never triggers a
  reminder, only a lock nobody has touched in hours does.
