# 0246 — Follow-up reminders (Wiedervorlagen) in favorite-service

**Status:** accepted
**Context:** P90-S1 (Phase 90, user-selected from the comparison with an external reference system).
Konzept 2.5 names the Wiedervorlage as an add-on: "documents/cases that are automatically resubmitted for
processing at a later, specific point in time — technically the same timer infrastructure as the
deletion reminder, 5.2a, and the SLA time monitoring, 7.1, just used for a different business purpose."

A person sets a date and a note on a document, folder or case. When the date comes, the object is put in
front of them again. They can complete the reminder or move it ("snooze").

## Decision 1 — favorite-service owns it

| Candidate | For | Against |
|---|---|---|
| **favorite-service** | Already the personal-object service: rows keyed by user name, an owner check on every call (ADR 0174), document/folder/case object types, no referential coupling. A reminder is a dated favorite with a note | — |
| notification-service | It sends the message | A reminder is user data with a lifecycle (open, snoozed, done), not a delivery; notification-service's own log is admin-only (`admin.notification_read`) and has no per-user view |
| workflow-service | Has timers | A process instance per reminder is heavy; BPMN adds nothing to "remind me on Tuesday" |
| document-/folder-/case-service | Next to the object | Three copies of the same feature |

**Chosen: favorite-service**, with a new table `reminder`. It differs from a favorite in two ways:
- several reminders per object are allowed ("in two weeks: check the reply; in three months: archive");
- it has a lifecycle: `open`/`done`, `due_at`, `notified_at`, `completed_at`.

**No referential or permission check on the object,** as for favorites:
- The reminder holds only the person's own note and an id.
- Showing the object still goes through the object's own permission checks.
- A reminder on an object the person can't (or can no longer) read leads to a 403 when they open it, not
  to a leak.

## Decision 2 — The timer: a poll in favorite-service, a message from notification-service

**The poll.** It is the pattern Konzept points to (deletion reminder, SLA):
- Every 60 s (`reminder_poll_interval_seconds`) it claims open, unnotified, due reminders, up to 500 per
  tick.
- It runs under `pg_try_advisory_xact_lock`, so with several replicas only one claims (as the alert
  evaluator, ADR 0245).

**Commit first, then publish.** It sets `notified_at`, commits, then publishes `favorite.reminder.due`
per reminder.
- A crash between commit and publish loses that one message.
- It does not lose the reminder: it stays listed as due until it is done or snoozed.
- At-least-once (publish before commit) would risk duplicate mails on every retry instead.

**notification-service consumes `favorite.reminder.due`:**
- **In-app** to the owner's user name.
- **E-mail** when the account has an address. It resolves the user name through auth-service's
  `GET /users/service-directory`, which it already uses for recipient checks.
- A failed lookup keeps the in-app message and skips the mail.
- **Template:** use case `favorite.reminder.due`, with placeholders `note`, `object_type`, `object_id`,
  `due_at`, `user`, `link`. Admins can word it in the e-mail templates page.
- **Link:** the existing direct link (ADR 0109) to the user-ui. `?case=` is new for cases; the user-ui
  reads it from P90-S2 on.
- **No title in the message.** Resolving titles would need a call per object type, as the owner. The
  note and the link carry what the person wrote down.

**In-app alone is not enough.** No user-ui view lists a person's own in-app notifications today
(notification-service's log is admin-only). So the primary channel is the reminder list itself
(`GET /reminders?due=true`, `GET /reminders/due-count` for a badge) in P90-S2. The in-app row stays for
the audit trail and a future inbox.

## Decision 3 — The lifecycle

- **Create:** `POST /reminders` with a time-zone-aware `due_at`. "09:00" without a zone would mean the
  server's zone. A past time is due at once.
- **Edit:** `PATCH` changes the note or the date. A new date re-arms the notification (`notified_at`
  cleared).
- **Snooze:** `POST /{id}/snooze` with `until` in the future; it re-arms.
- **Done and reopen:** `POST /{id}/done`; `/reopen` undoes a misclick. A reopened, past-due reminder is
  listed as due but not mailed again.
- **Delete:** `DELETE /{id}` removes it; there is no traceability requirement.
- **Owner only:** someone else's reminder answers 404, whatever the action. Its existence is nobody else's
  business.
- **Events:** `favorite.reminder.created/updated/snoozed/completed/deleted/due` on the `favorite` stream.
  audit-service already consumes `favorite.>`.

## Consequences

- favorite-service: `reminders.py`, the table with two indexes (per-user listing, due poll), seven
  endpoints, the poll loop.
- notification-service: the subject, the handler, `AuthServiceClient.email_for`, the template use case,
  and `case` links.

**Not in scope:**
- Reminders for someone else ("Wiedervorlage an Kollegin"). That needs a decision on whether the
  recipient must be able to read the object first. It is a candidate after P90-S3's recipient work.
- Recurring reminders.
- Shared team reminders.
- Clean-up of reminders on deleted objects (like favorites, they stay until the person removes them;
  the UI marks unresolvable objects).

**UI:** P90-S2.
