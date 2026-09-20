# favorite-service

**Purpose:** Favorites/watchlist (quick retrieval, since P7-S1d) — personal bookmarks by individual users on documents, folders, and cases (case-binder favorites added Phase 45 Session 2). Purely user-scoped (`user_id`/`object_type`/`object_id`), no relation to retention/deletion/approvals. *(User idea raised during the P7-S1b plan approval, deferred as its own session, see `PROGRESS.md`.)*

**Concept reference:** none (user request outside the original concept).
**Own Postgres schema:** `favorite` (table `favorite`).

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
- **Live smoke test** (P7-S1d): see `PROGRESS.md` — document/folder favorited via context menu, `FavoritesPane` resolved the names correctly, "Open" navigated correctly for both object types, audit trail showed `favorite.added`/`favorite.removed`.
- **Live smoke test** (Phase 45 Session 2): case favorited via the star toggle in `user-ui`'s `CasesPane` (both list and detail view), correctly listed in `FavoritesPane` with a resolved case name, "Open" navigated back into the case detail view.

## Open Points

- No referential check against document-/folder-/case-service (deliberate, see above) — a favorite on an object deleted in the meantime remains until the user manually removes it.
- No admin UI/configuration needed — a pure end-user feature with no four-eyes principle relevance.
