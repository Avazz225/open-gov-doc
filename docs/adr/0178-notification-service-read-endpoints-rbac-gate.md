# 0178 — notification-service: gate `GET /notifications`/`GET /notifications/{id}` behind a new capability

**Status:** accepted
**Context:** P59-S1 (Phase 59, "Critical Authorization Bugs" — first session of the sixth gap-analysis
round's live-code security sweep). `GET /notifications`/`GET /notifications/{id}` (`main.py`) had **no
permission check of any kind** — not even an `X-DMS-Principal` requirement. Any caller who could reach
the service through the gateway (any authenticated user, since the gateway's own authorization model
stops at "valid Keycloak JWT") could enumerate every notification ever sent, including its full
`subject`/`body`/`recipient`. Concretely this exposed break-glass superuser activation emails, emergency-
maintenance-mode alerts, virus-scan hits (with the uploader's identity), deletion reminders, and SLA
escalations. `POST /notifications` was already retrofitted with `_require_notification_permission` at
Post-Roadmap Phase 38 Session 2 — the read/list side was simply never given the equivalent treatment.

## Decision

Gate both `GET` endpoints behind a **new, third** notification-service capability,
`admin.notification_read` (role `domain-admin-notification-read`) — `401` without `X-DMS-Principal`,
`403` without the capability. No per-recipient self-service scoping (the plan's own initial framing
floated "an ordinary caller only sees their own recipient-matching notifications") — investigated and
declined once the actual real-world caller was traced: `admin-ui`'s `ProcessingFailuresView` is the ONLY
frontend consumer of these endpoints anywhere in this codebase, and it is an operational visibility/retry
tool for admins, not a personal-inbox view — no frontend anywhere lists "my own notifications" for an
ordinary user. An admin-only gate matches actual usage precisely; a per-recipient scope would have solved
a problem that doesn't exist, and couldn't even be defined cleanly regardless (`recipient` is a username
for most channels but an email address for `channel="email"`, so "the caller's own notifications" is not
a single well-defined comparison across channels).

## Rationale

- **Why a third, separate capability, not reusing `notification.write` or `admin.notification_config`**:
  each governs a materially different concern/risk profile. `notification.write` governs who may TRIGGER
  a notification (intended for a narrow automated caller like `reporting-service`'s scheduler, not a
  general "can read the log" grant). `admin.notification_config` governs who may change the WORDING every
  future notification is sent with (`EmailTemplate` CRUD). Reading the delivery log — full content of
  already-sent, potentially sensitive notifications — is neither of those. Same split-by-concern pattern
  this project already uses repeatedly (legal_hold vs. deletion, pseudonymization vs. reveal, retention vs.
  legal_hold).
- **Why not added to the "everyone" group**: unlike `audit.read`/`virus_scan.*`, this was never a
  de-facto-open-to-everyone feature that merely needed formalizing — it was simply never checked at all, a
  genuine oversight, not an intentional default-open design. The correct default for a capability that
  exposes PII/security-sensitive content across every user in the installation is closed, not open — same
  reasoning `_require_notification_permission` itself already used for `notification.write`.
- **Why `POST /notifications/{id}/retry` is NOT included in this fix**: also unauthenticated, but tracked
  separately as a Phase 61 medium finding (bundled with the webhook-SSRF finding in the same file) —
  narrower blast radius (a mutating retry of already-existing content, not a bulk-disclosure read), and
  bundling it here would have widened this session's scope beyond "the single most severe, fastest fix,
  do first."

## Consequences

- New domain-admin role `domain-admin-notification-read` (capability `admin.notification_read`),
  auto-seeded on every installation via the existing `ensure_domain_admin_roles` self-healing mechanism —
  no manual migration needed for a fresh install; an already-running installation gets the role created
  automatically on its next `permission-service` restart, same as every other domain-admin role addition
  in this project's history.
- `admin-ui`'s `ProcessingFailuresView` needs no code change (the gateway already forwards the real,
  JWT-verified `X-DMS-Principal` on every proxied call) — but an installation operator now needs to
  explicitly grant `domain-admin-notification-read` to whichever admin account should see this page,
  the same "backend-gated first, client-side UI consistency can follow later" pattern this project has
  used repeatedly (e.g. ADR 0148's own admin-ui alignment sweep); `ProcessingFailuresView` itself remains
  one of the admin-ui pages with no client-side capability gate, tracked separately (see
  IMPLEMENTATION_PLAN.md's Phase 62/"Deliberately Not Included in Phase 59+").
- New tests: `test_list_notifications_without_principal_header_is_401`,
  `test_list_notifications_without_read_permission_is_403`,
  `test_get_notification_without_principal_header_is_401`,
  `test_get_notification_without_read_permission_is_403` (4 new, `notification-service` 96/96 total).
  `permission-service` unchanged count (182/182), the new role is read dynamically from
  `DOMAIN_ADMIN_ROLES`, no dedicated test needed for the seed-list addition itself.
- Live-verified against the real running stack: `GET /notifications` and `GET /notifications/{id}` both
  `401` with no header, `403` for an authenticated principal without the new role, `200` once the role is
  granted — confirmed via direct `curl` against the real container, throwaway role assignment cleaned up
  afterward.
