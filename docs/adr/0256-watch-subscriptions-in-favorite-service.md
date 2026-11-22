# 0256 — Watch subscriptions: matched in favorite-service, delivered by notification-service

**Status:** accepted
**Context:** P90-S4 (Phase 90, user-selected from the comparison with an external reference system). The
plan text named this "ADR 0247", but P90-S3 used that number. 0248–0255 are reserved by later sessions,
so this is 0256.

A person wants to hear when a document or folder they care about changes: a new version, changed
metadata, a move, the trash, a deletion, and for a folder also a new document in it. document- and
folder-service already publish these events.

## Decision 1 — favorite-service owns watches and the matching

**Where watches live.** A watch is personal, like a favorite or a follow-up reminder (ADR 0246): keyed by
user name, owner-checked, on a document or folder. favorite-service gets a table `watch`, unique per
user and object. It also records the subscriber's `principal_id`, because permissions are keyed by
principal, not by user name.

**Where the matching happens.** Two options:
- **(a) notification-service consumes the domain events** and asks favorite-service who watches. That is
  a synchronous call per event on its hot path.
- **(b) favorite-service consumes them,** with the watches local, and publishes one
  `favorite.watch.triggered` per watcher.

**Chosen: (b).** The matching sits next to the data; notification-service only delivers, as for
reminders. The consumer uses one durable per stream (`favorite-service-watch-document`/`-folder`) with
`deliver_new`. Without it, the first start would replay the streams' whole history (over 200,000
document events on the dev stack) as notifications.

## Decision 2 — What counts as a change, and for whom

`watches.changes_for` (pure) maps:
- **Document:** `version.created` → new version; `metadata.updated` → metadata; `resource.moved` →
  moved; `deleted` → trash; `restored`; `force_deleted`/`trash_purged` → deleted.
- **Folder:** `updated` → renamed or changed; `resource.moved`, `trashed`, `restored`, `force_deleted`,
  `trash_purged`.
- **Inside a watched folder** (direct children, not the subtree): `document.created` with that
  `folder_id` → new document; `document.resource.moved` into it → moved in.

**`folder.updated` is new.** Until now a folder rename or attribute change published nothing, so it
was invisible to watchers and the audit trail. folder-service now publishes it with `name` and
`changed_fields`.

**Who is told:**
- **Not the person who made the change** (the event's `actor`).
- **Only someone who can still read the watched object.** The read check runs again before every
  notification (`check_batch` as the subscriber's principal), so a watch that outlives a permission
  change leaks nothing. Watching itself also needs read access (403 otherwise).
- **Deletions are exempt from the check:** there is no node left to check against, and "it is gone"
  discloses nothing the watcher didn't already watch.
- **A failing check skips that watcher** (fail closed) rather than failing the event, so one
  unreachable permission-service doesn't stall the stream.

## Decision 3 — Delivery and UI

**notification-service** consumes `favorite.watch.triggered` (second subject on the `favorite` stream,
own durable):
- In-app to the watcher, and a mail when the account has an address, the same path as reminders.
- Template `favorite.watch.triggered`; the fallback names the change in German.
- The link opens what changed: the new document for "neues Dokument", the watched object otherwise,
  nothing for a deleted one.

**user-ui:**
- "beobachten" / "nicht mehr beobachten" in the document and folder context menus.
- A "Beobachtet" section in the favorites view, with open and stop watching.

## Consequences

- **favorite-service:** `watch` table, `watches.py` (pure matching plus persistence),
  `watch_consumer.py`, `POST`/`GET`/`DELETE /watches`; depends on permission-service now (compose env,
  Helm `dependsOnServices`).
- **folder-service:** `folder.updated`.
- **notification-service:** the consumer and template.

**Not in scope:**
- Case and workflow-instance watches (the plan's "optionally").
- Recursive folder watches.
- Digest or batching of many changes. notification-service's per-recipient rate limiter caps bursts;
  a digest is a candidate if real use is noisy.
