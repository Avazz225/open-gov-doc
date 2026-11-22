# favorite-service

**Purpose:** Favorites/watchlist (quick retrieval, since P7-S1d) and, since P90-S1, follow-up reminders
(Wiedervorlagen, see below) — personal bookmarks by individual users on documents, folders, and cases (case-binder favorites added Phase 45 Session 2). Purely user-scoped (`user_id`/`object_type`/`object_id`), no relation to retention/deletion/approvals. *(User idea raised during the P7-S1b plan approval, deferred as its own session, see `PROGRESS.md`.)*

**Concept reference:** none (user request outside the original concept).
**Own Postgres schema:** `favorite` (tables `favorite`, `reminder` since P90-S1).

## Architecture decision: deliberately no referential check

Unlike, e.g., `case-service` (which actively validates its document references against `document-service`), `favorite-service` does **not** check on creation whether `object_id` actually exists. A favorite is a low-stakes personal bookmark, not a business data reference with a traceability requirement — an orphaned reference (e.g. after the original is deleted) causes no harm. Resolving the display name is handled by the calling UI (`user-ui`'s `FavoritesPane`, see `docs/services/user-ui.md`), which tolerates a 404 during resolution instead of failing the whole list. This keeps the service fully decoupled — no cross-service HTTP clients, no `depends_on` other than Postgres/NATS.

## Authorization: `user_id` ownership check (since P54-S3)

**Since P54-S3** ([ADR 0174](../adr/0174-favorite-service-user-id-check-against-x-dms-username.md)):
every endpoint checks that the request's `user_id` matches the caller's own identity — before this
session, `user_id` was a fully client-supplied field/query param with no check at all, so any
authenticated user could view/add/delete any OTHER user's favorites by passing a different `user_id`
(this service never read any `X-DMS-*` identity header before this session). `401` with no
`X-DMS-Username` header, `403` if `user_id` doesn't match it. Deliberately checked against
`X-DMS-Username` (Keycloak `preferred_username`), not the more obvious `X-DMS-Principal` (Keycloak
`sub`) — this service's `Favorite.user_id` rows have always been username-based in practice (`apps/
user-ui`'s `CasesPane.tsx` sends `useAuth().user.username`), so checking against `X-DMS-Principal`
would have rejected every real, already-existing favorites call in production. Both headers are equally
gateway-verified (see the ADR); this is purely a "which identifier scheme matches this service's own
data" question, not a trust difference.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/favorites` | Create (`user_id`, `object_type`: `"document"`\|`"folder"`\|`"case"`, `object_id`) — `201`, `409` if the favorite already exists (unique constraint `user_id`+`object_type`+`object_id`). **Since P54-S3**: `user_id` must match the caller's own `X-DMS-Username`, see below |
| `GET` | `/favorites` | List for a user (`user_id` required query parameter, `object_type` optionally filterable), newest first. **Since P54-S3**: `user_id` must match the caller's own `X-DMS-Username` |
| `DELETE` | `/favorites` | Remove via query parameters (`user_id`, `object_type`, `object_id`) instead of a path `id` — the caller (context menu) only knows the favorited object, not the internal favorite `id`. `404` if not favorited. **Since P54-S3**: `user_id` must match the caller's own `X-DMS-Username` |
| `GET` | `/healthz` | Health check |

## Data Model

- `favorite`: `id` (UUID), `user_id`, `object_type` (`"document"`\|`"folder"`\|`"case"`), `object_id`, `created_at`. Unique constraint on `(user_id, object_type, object_id)` — prevents duplicates, no soft delete (a removed favorite is hard-deleted, there is no traceability requirement like with the deletion register).

## Events

Published (stream `favorite`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `favorite.added` | `{user_id, object_type, object_id}` |
| `favorite.removed` | `{user_id, object_type, object_id}` |

No own consumer — this service does not react to events from other services.

**Audit integration**: since this session, Audit Service additionally consumes `favorite.>` (same immediate-addition pattern as for every previous new producer stream).

## Self-registration (Concept 3.2a)

Registers itself with the registry on startup (`libs/dms-registry-client`), identical pattern to every other service. Opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`. The gateway requires no code change of its own — routing runs fully dynamically via `service_type="favorite-service"`.

## Tests

- `uv run pytest services/favorite-service/tests`: Repository (create, duplicate rejection, remove including `NotFoundError`, list filtering by user/object type, newest-first sorting, case favorite creation), API (`POST`/`GET`/`DELETE` including `409`/`404`, filtering by `object_type`, user isolation, full case favorite create/list/delete round trip). **18 tests since P54-S3** (+4: `test_create_favorite_for_another_user_is_rejected`, `test_create_favorite_without_principal_header_returns_401`, `test_list_favorites_for_another_user_is_rejected`, `test_delete_favorite_for_another_user_is_rejected` — the latter also confirms the target favorite genuinely survives a rejected cross-user delete attempt, not just that the response code is correct; every existing test's `client` call sites updated to send a matching `X-DMS-Username` header via a new `_headers()` test helper). Before P54-S3, 14 tests since Phase 45 Session 2.
- **Since P90-S1:** 25 tests (+7). `test_reminders.py` covers:
  - owner-only access (403/401/404);
  - the time-zone requirement;
  - the due listing, count and one notification per due time;
  - snooze and edit re-arming;
  - done, reopen and delete;
  - the object/status filters.

  `test_repository.py` covers one replica claiming a tick while another holds the lock. Run through
  `scripts/run-tests.sh favorite-service`.
- **Live smoke test** (P7-S1d): see `PROGRESS.md` — document/folder favorited via context menu, `FavoritesPane` resolved the names correctly, "Open" navigated correctly for both object types, audit trail showed `favorite.added`/`favorite.removed`.
- **Live smoke test** (Phase 45 Session 2): case favorited via the star toggle in `user-ui`'s `CasesPane` (both list and detail view), correctly listed in `FavoritesPane` with a resolved case name, "Open" navigated back into the case detail view.

## Acting Identity (P85-S4, [ADR 0240](../adr/0240-acting-identity-from-verified-principal.md))

Already compliant, unchanged: `user_id` must equal the gateway-verified `X-DMS-Username`
(`_require_own_user_id`), so nobody can create favorites for someone else. Favorites stay keyed by user
name; rebinding them to the `sub` would orphan existing rows for no security gain.

## Follow-up reminders (Wiedervorlagen, P90-S1, [ADR 0246](../adr/0246-follow-up-reminders-in-favorite-service.md))

A person puts a document, folder or case back in front of themselves at a set time, with a note
(Konzept 2.5's named add-on). Personal like favorites: the same `X-DMS-Username` owner rule, no
referential check, several reminders per object.

| Method | Path | Description |
|---|---|---|
| `POST` | `/reminders` | `{user_id, object_type, object_id, due_at, note}`. `due_at` must carry a time zone (`422` otherwise); a past time is due at once. `201` |
| `GET` | `/reminders?user_id=` | The caller's reminders, earliest due first. Filters: `status` (`open`/`done`), `due=true` (open and due), `object_type`+`object_id` (one object). Each row has a computed `is_due` |
| `GET` | `/reminders/due-count?user_id=` | `{due: n}` for a badge |
| `PATCH` | `/reminders/{id}` | `{due_at?, note?}` on an open reminder (`409` when done). A new date re-arms the notification |
| `POST` | `/reminders/{id}/snooze` | `{until}`, which must be in the future (`422` otherwise); re-arms |
| `POST` | `/reminders/{id}/done` | Completes (`409` when already done) |
| `POST` | `/reminders/{id}/reopen` | Undoes "done" |
| `DELETE` | `/reminders/{id}` | `204` |

- **Owner check:** someone else's reminder answers `404`, whatever the action; a missing user name
  answers `401`.
- **Table:** `reminder` (`id`, `user_id`, `object_type`, `object_id`, `due_at`, `note` ≤ 2000, `status`,
  `notified_at`, `completed_at`, `created_at`, `updated_at`). Indexed for the per-user listing and the
  due poll.

**The due poll:**
- **Cadence:** every `reminder_poll_interval_seconds` (60), first after
  `reminder_poll_initial_delay_seconds` (2).
- **One replica per tick:** under `pg_try_advisory_xact_lock`, so with several replicas only one
  claims.
- **What a tick does:** claims up to 500 open, unnotified, due reminders, sets `notified_at`, commits,
  then publishes `favorite.reminder.due` per reminder with actor `system:favorite-reminders`.
  notification-service turns that into an in-app message and, when the account has an address, a mail.
- **Commit before publish:** a lost event costs a message, not the reminder; it stays listed as due.

**Events:** `favorite.reminder.created/updated/snoozed/completed/deleted/due`, with payload `user_id`,
`object_type`, `object_id`, `due_at` (and `note` on `due`).

**Live (P90-S1):**
- A due reminder for `users-admin` (no e-mail address) produced the in-app notification only.
- One for a temporary user with an address also produced the mail "Wiedervorlage: Mail-Pfad prüfen"
  with the note and the direct link.
- Both reminders and the user were deleted afterwards.

## Watch subscriptions (P90-S4, [ADR 0256](../adr/0256-watch-subscriptions-in-favorite-service.md))

A person is told when a watched document or folder changes. One watch per user and object, owner-checked
like favorites.

| Method | Path | Description |
|---|---|---|
| `POST` | `/watches` | `{user_id, object_type: document\|folder, object_id}`. Needs read access (403 otherwise), checked as the caller's `X-DMS-Principal`, which is stored. `409` when already watched |
| `GET` | `/watches?user_id=` | Own watches, optionally by `object_type`/`object_id` |
| `DELETE` | `/watches?user_id=&object_type=&object_id=` | `204`, `404` when not watched |

**Matching (`watches.changes_for`, pure):**
- **Document:** new version, metadata, moved, trash, restored, deleted.
- **Folder:** `folder.updated` (rename/attributes, published since this session), moved, trash,
  restored, deleted.
- **Inside a watched folder** (direct children): a new document, or one moved in.

**The consumer (`watch_consumer.py`):**
- one durable per stream (`favorite-service-watch-document`/`-folder`, `deliver_new`);
- publishes `favorite.watch.triggered` per watcher (`change`, `subject_type`/`subject_id`, `title`,
  `changed_by`), which notification-service delivers in-app and by mail.

**Who is told:**
- not the person who made the change;
- only a watcher who can still read the object (read check before every notification; deletions
  exempt);
- a failing check skips that watcher.

**Configuration:** `permission_service_base_url`, `watch_subjects`, and `watch_consumer_enabled`
(false in tests).

**Events:** `favorite.watch.added`/`.removed`/`.triggered`.

**Live (P90-S4):**
- A temporary user watched a folder.
- `users-admin` uploaded a document into it and renamed it. Two mails arrived: "Beobachtet: Ordner –
  neues Dokument" (with the document title and its link), and the rename (with the new name and the
  folder link).

## Open Points

- No referential check against document-/folder-/case-service (deliberate, see above) — a favorite on an object deleted in the meantime remains until the user manually removes it.
- Reminders, like favorites, stay on deleted objects until the person removes them. Reminders for
  someone else, recurring and team reminders are out of scope (ADR 0246).
- No admin UI/configuration needed — a pure end-user feature with no four-eyes principle relevance.
