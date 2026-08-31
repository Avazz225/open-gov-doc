# 0123 — Multi-inbox model: env-var config (not DB CRUD), department RBAC deferred

**Status:** accepted (P31-S12a, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 12a (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #6), affects `mail-connector`,
`infra/docker-compose.yml`; first of a three-part split (12a config/plumbing → 12b routing/hand-off →
12c searchable Postbuch log)

## Decision

Both the gap-analysis authors and `IMPLEMENTATION_PLAN.md` itself flagged the full "central + decentralized
inbox model with a cross-inbox routing registry ('Postbuch')" ask as too large for one session. A research
pass grounded in the actual code (mail-connector's single, service-wide POP3/IMAP/SMTP `Settings` block,
baked into `main.py`'s lifespan/poll loop) confirmed three natural, independently-shippable pieces, put to
the user directly: (A) multi-inbox configuration/plumbing, (B) routing/hand-off semantics between inboxes,
(C) the searchable Postbuch log UI. **The user chose to split into P31-S12a/b/c** rather than attempt all
three in one pass. This ADR covers only 12a — the config/plumbing foundation the other two build on.

A second decision was needed before designing 12a: nothing in this codebase models a "department" today.
**The user chose to piggyback a departmental mailbox's owner on `permission-service`'s existing `Group`**
(by `Group.id`, cross-service reference, no FK enforcement — the same convention `folder_id`/
`object_type_id` already use elsewhere) rather than invent a second, competing org-unit concept — consistent
with P31-S9's own explicit choice not to build a dedicated org-unit entity (ADR 0120).

`Settings.mailboxes: list[MailboxConfig]` replaces the previous single `inbound_protocol`/`pop3_*`/
`imap_*` block — each entry carries `id`, `name`, `kind` (`"central"`|`"departmental"`),
`owning_group_id` (required iff `kind="departmental"`), and its own POP3/IMAP connection details.
`InboundMessage` gained `mailbox_id`, and `source_uid`'s uniqueness became scoped per mailbox
(`UNIQUE(mailbox_id, source_uid)`, migrated via an ad-hoc `ALTER TABLE`). The poll loop now iterates every
configured mailbox each tick (one backend instance per mailbox, built once at startup). New
`GET /mailboxes` (credential-free) and an optional `mailbox_id` filter on `GET /inbound`.

## Rationale

- **Env-var-list config, not a DB-backed CRUD table** — the same shape and the same reasoning as
  `storage-service`'s `BackendTargetConfig`/`DMS_TARGETS` (ADR 0004/0017): mailbox connection details
  include real credentials (POP3/IMAP passwords), and [ADR 0091](0091-connector-operational-config-live-editable.md)
  already established, for this exact class of problem (storage/signature connector config), that making
  credentials live-editable needs new encryption/masking infrastructure a `GET` response must never leak
  plaintext through — explicitly out of scope there, and out of scope here for the same reason. A DB-backed
  `Mailbox` table with admin CRUD would have silently taken on that unscoped work. `GET /mailboxes`
  deliberately returns a credential-free projection (`MailboxOut`), never the raw config.
- **One poll loop iterating N mailboxes, not N concurrent poll tasks**: matches this project's established
  poll-loop idiom (simple, sequential, one `try`/`except` per tick) rather than introducing per-mailbox
  concurrency control; `Pop3Backend`/`ImapBackend` both connect fresh per `fetch_new_messages()` call (no
  persistent connection state), so sequential iteration costs nothing meaningful at mail-room-operation
  volume. Each mailbox's fetch/ingest is wrapped in its OWN `try`/`except` within the tick, so one
  mailbox's transient failure doesn't block the others from being polled in the same pass.
- **`source_uid` uniqueness scoped per mailbox, not left globally unique**: a POP3/IMAP UID is only
  guaranteed stable and unique *within* one mail account (RFC 1939) — two different mailboxes could
  plausibly reuse the same native UID coincidentally, they're different accounts. The existing single-column
  unique constraint would have silently rejected a second mailbox's legitimate first message if its UID
  happened to collide with an existing one from another mailbox.
- **Existing `docker-compose.yml`/`.env.example` config migrated directly, not kept on a legacy fallback
  path**: the old `DMS_POP3_HOST`/`DMS_POP3_USERNAME`/etc. env vars are gone, replaced by `DMS_MAILBOXES`
  (JSON list) with a single default entry (`id="central"`) reproducing the exact previous single-mailbox
  behavior against the same `mailpit` self-loopback dev target. This is a real, one-time compose-file
  migration this session performs directly (updating infrastructure config it controls), not a
  backwards-compatibility shim carried forward in code — consistent with this project's stated preference
  for changing the code/config directly over accumulating legacy-path branches.
- **Deliberately NOT nesting `${VAR}` references inside `DMS_MAILBOXES`'s default JSON value**: an initial
  draft tried `"pop3_username":"${MAIL_CONNECTOR_POP3_USERNAME:-mailconnector}"` inside the outer
  `${MAIL_CONNECTOR_MAILBOXES:-...}` default — found and reverted before shipping, since docker-compose's
  variable interpolation is not brace-nesting-aware (a single regex pass over the whole string, terminating
  at the first `}` found), so the inner reference would have silently truncated the outer one. Same,
  already-documented caveat as `STORAGE_SERVICE_TARGETS`'s own comment ("env var values are not expanded
  further ... credentials must be entered literally") — this session's default JSON now also states
  credentials literally, no partial nested override.
- **Migration backfills every existing row to `mailbox_id="central"`** (the fixed id the new single-default
  mailbox entry uses) rather than leaving pre-existing rows with a sentinel/`NULL` value — every message
  that ever arrived did so through what is now named the "central" mailbox; backfilling to that exact id
  keeps `GET /inbound?mailbox_id=central` meaningfully complete for installations upgrading from the
  single-mailbox model, not just for newly-arriving mail.
- **RBAC deliberately UNCHANGED in this session** — `poststelle_role` remains one single, global role;
  every principal holding it sees every configured mailbox, central and departmental alike, exactly as
  today's single-mailbox model already behaves. The research that grounded this session's scoping
  explicitly flagged "departmental staff should presumably only see their own department's inbox" as "a
  real RBAC redesign, not just a filter" — a materially separate design problem (whether it's a new
  permission-service resource type for mailboxes, a `Group`-membership check layered onto the existing role
  gate, or something else) deliberately left open rather than rushed into this config/plumbing session.
  `owning_group_id` is stored and exposed (`GET /mailboxes`) specifically so a later session has the data
  it needs without this one having to design the enforcement.
- **Outbound/SMTP stays a single, global config** — the plan's own wording is about "inbox" specifically
  (central + per-department *inboxes*); multi-identity sending (e.g. "reply as the Finanzen department")
  is a different, unscoped feature this session doesn't attempt.

## Consequences

- **No per-mailbox/department RBAC narrowing yet** — a genuine, honestly-documented gap: any
  `poststelle_role` holder currently sees and can act on every mailbox's mail, central and departmental
  alike, identical to today's undifferentiated visibility. A follow-up session (not scoped as 12b/12c,
  which are about routing/search, not visibility) would need to design this properly — likely either a new
  permission-service resource type for mailboxes, or a `Group`-membership check layered onto
  `_require_poststelle`.
- **Adding, removing, or reconfiguring a mailbox still requires an env var change + service restart** — no
  admin-UI touchpoint in this session (consistent with the DB-CRUD-avoidance decision above; `storage-
  service`/`signature-service`'s connector *lists* are equally restart-only, ADR 0091).
- **No frontend changes in this session** — `PoststellePane.tsx` (user-ui) is untouched; it still shows
  every inbound message regardless of `mailbox_id` (harmless today since there's still only one configured
  mailbox in the dev stack, but a real second departmental mailbox would appear in the same undifferentiated
  list without a selector to narrow by). A mailbox selector/filter UI is deferred, most naturally alongside
  12b/12c once there's real routing behavior to also surface there.
- **`mailbox_id` has no FK/existence check against `Settings.mailboxes`** — same deliberate pattern as
  `folder_id`/`object_type_id` elsewhere in this project (config-space references aren't DB-enforced across
  the config/table boundary); removing a mailbox from `DMS_MAILBOXES` leaves its historical messages'
  `mailbox_id` pointing at a now-unconfigured id, still queryable, just no longer pollable.
