# 0259 — Listings show only what the caller can read

**Status:** accepted
**Context:** P91-S4. User request 2026-11-21: folders the user can't open should be hidden from listings,
for a clearer overview.

## The gap

- **Folders:** `GET /folders/{id}/children` checked `folder.read` on the parent only and returned every
  child. That included folders with their own permissions, for example a teamspace root
  (`inherit=False`, ADR 0149). Their names and existence leaked to everyone.
- **Documents:** every document has had its own resource node since P39-S4 (ADR 0154), so a document
  can be closed inside a readable folder. `GET /documents?folder_id=` checked the folder only.
- Opening such an object was already refused (`403`). Only the listings leaked.
- **Who lists:**
  - the user UI (list and tree);
  - WebDAV and CMIS, through the connector SDK and with the user's principal;
  - migration-service, with its own service principal.

## Decision 1 — Filter server-side, one batch check per listing

- folder-service filters `children` by `folder.read`, and document-service filters the folder listing by
  `document.read`.
- Each listing makes one `POST /check/batch` to permission-service.
- Every client gets the same view without changes: the list and tree views, WebDAV, CMIS.
- Hidden objects are simply absent. There is no count and no "n hidden" for users, because even a count
  says something exists.

## Decision 2 — Services learn how much was hidden; a migration fails instead of skipping

A migration copies a subtree with its own principal. Before, an unreadable subfolder made it fail
loudly (`403` on the listing below it); with filtering it would silently copy less.
- **For service principals only** (`SERVICE_PRINCIPALS`), both listings set `X-DMS-Hidden-Count`.
- The connector SDK's new `list_children_complete` raises `HiddenChildrenError` when the count is
  above zero.
- migration-service's copy step uses it, so a transfer fails with "n Einträge ohne Leserecht des
  Dienstes ausgeblendet" instead of quietly leaving something out.

## Decision 3 — An unreadable ancestor on a breadcrumb is a placeholder

A folder can be readable below an unreadable one, for example through a direct grant, a favorite or a
link.

**Options:**
- **(a) Hide the ancestor:** the path looks shorter than it is, and the readable folder appears to sit
  somewhere else.
- **(b) A neutral placeholder:** id and position only. The folder's own position already implies that an
  ancestor exists.

**Chosen: (b).** `GET /folders/{id}/breadcrumb` returns an unreadable ancestor with `restricted: true`:
an empty name, no type, no attributes, no creator, and a fixed timestamp. No UI uses the breadcrumb
endpoint today; a client that shows paths renders it as "🔒".

## Decision 4 — The batch check had to become a real batch

**Measured on the dev stack, through the gateway, median of 15:**

| Listing | Before (unfiltered) | Filtered, old batch | Filtered, new batch |
|---|---|---|---|
| Root folder, 810 children | 19 ms | 1,038 ms | 48 ms |
| Top-level documents, 538 | 25 ms | 710 ms | 37 ms |

**Why the old batch was slow:** `/check/batch` looped over the ids, and each id meant:
- a generation read and a cache read;
- a scope-lock walk with two queries per ancestor level.

That is several thousand sequential queries for one root listing.

**Now:**
- all cached entries for the principal are read in one query (`get_effective_permissions_batch`), and
  only misses are computed individually;
- active scope locks are loaded once, and the per-resource walk only runs if any lock is active at all
  (usually none).
- A test compares the batch with single `/check` calls for warm and cold entries and after a permission
  change (a new cache generation).

The remaining overhead is about 30 ms on the largest listing of the dev stack. The P95-S1 load test
measures it under load.

## Found on the way: a pool self-deadlock in auth-service

- **Symptom:** parallel e2e runs failed in the login.
- **Measurement:** with five or more concurrent logins, every login took exactly 30 s, and some failed
  with `QueuePool limit … reached`.
- **Cause:** the login's session tracking opened a second session (`superuser.get_status`,
  `get_principal_id`) while the request's own still held a connection.
  - The pool budget of P89-S4 (ADR 0254) gives auth-service 2 + 3 connections: five logins each held
    one and waited for a second until the pool timeout.
  - Before P89-S4 the threshold was 15, so it never showed.
- **Fix:**
  - `superuser.active_principal_id(session)` reads through the request's session;
  - and bcrypt (about 0.2 s of CPU) runs in a worker thread, so logins no longer serialise every other
    request to auth-service.
- **Result:** eight parallel logins in under 0.8 s, none failing.
- **Lesson for Phase 94/95:** look for any other nested `session_factory()` inside a request that holds
  a session. A pool smaller than the concurrency turns each one into a deadlock.

**Also noted:** the gateway's per-principal rate limit (600 per minute) is reached when the e2e suites
run back to back as one user. That is expected behaviour, not a bug. One run per minute passes.

## Also: document-service's intermittent test hang is still unexplained

- **What happened:** the suite hung again in this session (32 minutes, then ended), in the same place
  as in P90-S5 and P91-S2b: `TestClient.__exit__` waits for the app's lifespan shutdown.
- **A correction:** P91-S2b's NATS consumer debris was not the cause. NATS was clean this time.
- **Reproduction failed:** seven runs afterwards passed, alone and in the hung run's order (permission →
  folder → document), with a plugin that would have dumped every waiting asyncio task.
- **What was done instead:** every shutdown step of document-service is now bounded to 10 s and logged
  by name (`shutdown_step_timeout: …`).
  - A pod's shutdown or a test teardown can no longer hang on one step.
  - The next occurrence names the step in the log.
  - The diagnostic plugin is in the session scratchpad (`diag/teardown_watchdog.py`).

## Upload by right-click

"Hochladen in "…" …" in the folder context menu, for list and tree, uploads into that folder (not
the one shown). The dialog names its target. Dropping files onto a tree folder stays optional and wasn't
built.

## Consequences

- **folder-service:** children filtered, the hidden count for services, breadcrumb placeholders.
- **document-service:** the folder listing filtered, the hidden count for services.
- **permission-service:** the batch check reads cached entries in bulk and skips the scope-lock walk
  when no lock is active.
- **Connector SDK:** `list_children_complete` and `HiddenChildrenError`. **migration-service** uses
  them.
- **auth-service:** no nested session in the login, and bcrypt in a thread.
- **user-ui:** "Hochladen in …" in the folder context menu.
