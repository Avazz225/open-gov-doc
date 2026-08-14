# favorite-service

**Purpose:** Favorites/watchlist (quick retrieval, since P7-S1d) — personal bookmarks by individual users on documents and folders. Purely user-scoped (`user_id`/`object_type`/`object_id`), no relation to retention/deletion/approvals. *(User idea raised during the P7-S1b plan approval, deferred as its own session, see `PROGRESS.md`.)*

**Concept reference:** none (user request outside the original concept).
**Own Postgres schema:** `favorite` (table `favorite`).

## Architecture decision: deliberately no referential check

Unlike, e.g., `case-service` (which actively validates its document references against `document-service`), `favorite-service` does **not** check on creation whether `object_id` actually exists. A favorite is a low-stakes personal bookmark, not a business data reference with a traceability requirement — an orphaned reference (e.g. after the original is deleted) causes no harm. Resolving the display name is handled by the calling UI (`user-ui`'s `FavoritesPane`, see `docs/services/user-ui.md`), which tolerates a 404 during resolution instead of failing the whole list. This keeps the service fully decoupled — no cross-service HTTP clients, no `depends_on` other than Postgres/NATS.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/favorites` | Create (`user_id`, `object_type`: `"document"`\|`"folder"`, `object_id`) — `201`, `409` if the favorite already exists (unique constraint `user_id`+`object_type`+`object_id`) |
| `GET` | `/favorites` | List for a user (`user_id` required query parameter, `object_type` optionally filterable), newest first |
| `DELETE` | `/favorites` | Remove via query parameters (`user_id`, `object_type`, `object_id`) instead of a path `id` — the caller (context menu) only knows the favorited object, not the internal favorite `id`. `404` if not favorited |
| `GET` | `/healthz` | Health check |

## Data Model

- `favorite`: `id` (UUID), `user_id`, `object_type` (`"document"`\|`"folder"`), `object_id`, `created_at`. Unique constraint on `(user_id, object_type, object_id)` — prevents duplicates, no soft delete (a removed favorite is hard-deleted, there is no traceability requirement like with the deletion register).

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

- `uv run pytest services/favorite-service/tests`: Repository (create, duplicate rejection, remove including `NotFoundError`, list filtering by user/object type, newest-first sorting), API (`POST`/`GET`/`DELETE` including `409`/`404`, filtering by `object_type`, user isolation). **12 tests, all passing.**
- **Live smoke test** (P7-S1d): see `PROGRESS.md` — document/folder favorited via context menu, `FavoritesPane` resolved the names correctly, "Open" navigated correctly for both object types, audit trail showed `favorite.added`/`favorite.removed`.

## Open Points

- No referential check against document-/folder-service (deliberate, see above) — a favorite on an object deleted in the meantime remains until the user manually removes it.
- No admin UI/configuration needed — a pure end-user feature with no four-eyes principle relevance.
- No cases (`case-service`) — deliberately limited to documents/folders at plan approval, since cases currently have no dedicated UI in `user-ui` (see `PROGRESS.md`).
