# search-service

**Responsibility:** Full-text index + facet search over document metadata and content (concept 3.7) — indexes documents from metadata events, enriches them with OCR/rendering full text as soon as it becomes available, and filters results according to the actual read permissions of the searching principal. Since P14-S7 (3.7a) with an extended query language (boolean combination with precedence/grouping, phrases, wildcards, fuzzy and proximity search).

**Concept reference:** 3.7, 3.7a
**Own Postgres schema:** `search` (`search_document`).

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/search?q=...&folder_id=...&object_type_id=...&created_by=...&created_after=...&created_before=...&attr.{name}[.gte\|.lte]=...&limit=&offset=&sort=` | Search + facet filtering. `q` optional (empty = pure facet navigation), otherwise parsed via the extended query language (see below) — on a syntax error (e.g. a missing closing parenthesis) `400` with a plain-text error message. Requires the `X-DMS-Principal` header injected by the gateway — `401` if missing. |
| `GET` | `/search/facets` | Available object types incl. attribute schema (for the filter UI) |
| `GET` | `/healthz` | Health check |

## Query Language (3.7a, since P14-S7)

`q` is interpreted via its own, small parser (`query_language.py`, recursive descent over a hand-written tokenizer) instead of the previous direct `websearch_to_tsquery(query)`:

| Syntax | Meaning |
|---|---|
| `word1 word2` | Implicit AND |
| `word1 OR word2` | Disjunction (case-insensitive) |
| `-word` / `NOT word` | Negation |
| `(...)` | Explicit grouping/precedence (precedence otherwise: parenthesis > NOT > AND > OR) |
| `"exact phrase"` | Phrase search (exact word sequence) |
| `"word1 word2"~N` | Proximity search — exactly two words, matches if they occur in either order within `N` words of each other (`N` clamped to a maximum of 20) |
| `word*` | Wildcard/prefix search |
| `word~` / `word~N` | Fuzzy search (typo tolerance via `pg_trgm`), tolerance level `N` ∈ {1,2,3} (1 = strict, 3 = tolerant), default 2 without a number |

`query_compiler.py` translates the resulting AST into SQLAlchemy expressions: without a fuzzy leaf, the whole tree is assembled into ONE `to_tsquery('german', ...)` string (Postgres' own operator algebra handles precedence/grouping, ranking remains a single `ts_rank()` as before this session); as soon as a fuzzy leaf occurs anywhere, it is instead compiled leaf-by-leaf into SQL boolean expressions (mathematically exactly equivalent, see [ADR 0044](../adr/0044-search-query-language-fuzzy-proximity.md) for the rationale), where ranking is a simple sum of independent per-leaf scores. Fuzzy matching uses `pg_trgm`'s `word_similarity()` against `title`/`full_text`, with dedicated trigram GIN indexes (`CREATE EXTENSION IF NOT EXISTS pg_trgm` + two `gin_trgm_ops` indexes, created idempotently in the lifespan like every other ad-hoc schema change in this project).

`attr.*` filters require `object_type_id` (the attribute type is only known via the object-type schema) — without `object_type_id`, an `attr.*` parameter returns `400`.

## Data Model

`search_document`: one entry per **document** (natural key `document_id`, not per version — search reflects the current state, not the history). Fields: `title`, `folder_id`/`folder_name` (denormalized), `object_type_id`, `attributes` (`postgresql.JSONB` — deliberately not the generic `JSON` used by document-service/ocr-service, since JSONB cleanly supports the `->>` operations needed for attribute filters), `current_version_number`, `full_text`, `created_by`/`created_at`/`updated_at`, `indexed_at`, `search_vector` (`TSVECTOR`, GIN-indexed). Soft-deleted documents are **hard-removed from the index** (`DELETE`), not just marked — there is no UX requirement to display deleted hits. Since P14-S7, additionally two trigram GIN indexes on `title`/`full_text` (`gin_trgm_ops`, for fuzzy search, see above).

`search_vector` is computed in `repository.upsert_document()` via a raw SQL `UPDATE` after every flush (`setweight(to_tsvector('german', title), 'A') || setweight(to_tsvector('german', full_text), 'B')`, title hits are weighted higher) — deliberately not a generated Postgres column, so the weighting logic stays visible/testable here instead of being hidden in the DDL.

## Indexing Pipeline

Consumes **three** NATS subject groups via the same `event_bus` client, but two separate durable consumers (no publisher — Search Service publishes no events of its own, the same pure consumer role as audit-service):

- **`document.>`** (durable `search-service`): `document.deleted` deletes the index row; `document.created`/`document.version.created`/`document.metadata.updated` trigger `reindex_document()`.
- **`ocr.>` + `rendering.>`** (durable `search-service-text`): `ocr.completed` (status `ready`/`needs_review`) and `rendering.completed` (only `rendition_type=="substitute_text"`, status `ready`) likewise trigger `reindex_document()`.

**Document events are deliberately thin** (`document.created` only supplies `{title, created_by}`, `document.version.created` only `{version_number, is_conflict, created_by}`, `document.metadata.updated` only `{title}`) — `reindex_document()` reloads the full record via `GET /documents/{id}` on every call, regardless of which event triggered the call.

**Cross-stream backfill race, deliberately resolved via a single code path**: `document.>` and `ocr.>`/`rendering.>` are separate JetStream streams with no ordering guarantee relative to each other — on the very first start of a fresh search-service consumer (no `deliver_new`, replays the complete history), an `ocr.completed` for a document can arrive before its `document.created` has been processed. Since `reindex_document()` reloads the full document state via HTTP in both cases (instead of relying on an already-existing index row), the arrival order is irrelevant — there is only one code path for "what should this document's index row look like right now", not two diverging ones ("create new" vs. "just update text").

**Full-text origin**: the OCR result (`GET /ocr-results` on the OCR Service) is preferred; the `substitute_text` rendition (`GET /renditions` on the Rendering Service, filtered client-side to `rendition_type=="substitute_text"`, since the endpoint itself does not filter by it) is the fallback once neither is available. On a version change (`current_version_number` differs from the already-indexed state), `full_text` is first reset until the new version has received its own OCR/rendering result — no carrying over of stale content.

## Permission Filtering (3.1, critical finding of this session)

**Documents themselves are not permission resources.** `permission-service` only registers folders as `ResourceNode` (`structure_subjects = ["folder.>"]`) — `document-service` never calls `permission-service` anywhere. A search result is therefore checked via its **`folder_id`**, not via `document_id` (documents without a `folder_id` are mapped to the resource `"root"`).

Flow in `GET /search`: `principal_id` is read from the `X-DMS-Principal` header injected by the gateway (JWT `sub`, `services/gateway-service/src/gateway_service/main.py`) — **not** a query parameter, to rule out forgery by the client. The SQL search first runs with an overfetch margin (`(limit+offset) * search_result_overfetch_factor`, hard-capped at `search_result_hard_limit`), since only afterward is it checked via `POST /check/batch` on the Permission Service (new in this session, see `docs/services/permission-service.md`) which of the affected `folder_id`s the principal is allowed to read — unreadable hits are removed from the list (no "locked" marker, since there is no existing UI pattern for that), and only then is pagination applied.

## Backend Connection

- **Document Service** (3.1): `GET /documents/{id}` — full metadata state after every event.
- **Folder Service** (3.1): `GET /folders/{id}` — denormalization of `folder_name` (no code change needed on Folder Service).
- **Object-Type Service** (3.1, a pure reference-data service without its own events): `GET /object-types`/`GET /object-types/{id}` — queried synchronously for facet definitions and attribute-filter type resolution (no code change needed).
- **Permission Service** (3.1): `POST /check/batch` — permission filtering, see above.
- **OCR Service** (3.9, P5-S3) / **Rendering Service** (3.7, P5-S2): full-text sources, see indexing pipeline above.

## Why No Audit Extension (deliberately assessed and rejected)

Search Service publishes no events of its own — 5.3 requires auditing of document-processing operations (upload, scan, rendering, OCR), not of read/search operations. `audit-service` therefore remains unchanged.

## Self-Registration (Concept 3.2a)

Registers itself with the registry on startup via `dms-registry-client` — opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Sensors (Concept 10.1)

None yet — follows in Phase 11.

## Tests

- `uv run pytest services/search-service/tests`: repository (upsert/delete, `search_vector` weighting title vs. full text, attribute filters per type — exact string, decimal/date range —, facet grouping), pipeline (`reindex_document` against the real running Document/Folder Service incl. folder-name denormalization, version-change reset, deleted/unknown document), consumer integration (a real NATS event triggers real indexing; an explicit regression test for the cross-stream backfill race — an `ocr.completed` event without a previously processed `document.created` still produces a complete index row), API (`/search`/`/search/facets` incl. real permission filtering against the running Permission Service, `401` without the principal header).
- **Since P14-S7: 56 tests** (previously 21, **+35**) — new `test_query_language.py` (23 pure parser/tokenizer tests without a DB: AND/OR/NOT precedence, grouping, phrases, proximity search incl. word-count/distance validation, fuzzy-level validation, the `E-Mail` hyphen special case, syntax errors), ten new `test_repository.py` cases against real Postgres (boolean AND/OR/NOT, grouping, phrase word order, prefix wildcard, fuzzy — both an actual typo hit and a deliberately too-dissimilar query with no hit even at the strict level —, proximity search incl. a "too far apart" counter-example, `QuerySyntaxError` on an invalid query), two new `test_api.py` cases (`400` on a missing closing parenthesis over HTTP, a fuzzy hit via the full `TestClient` path).
- **Important test finding of this session**: `TestClient(app)`-based tests (API/consumer integration tests) connect via the `DMS_POSTGRES_DSN` environment variable read by the service itself — **not** via `TEST_POSTGRES_DSN`, which only affects the engines built directly in `conftest.py`/repository tests. For an isolated test database, **both** variables must therefore be set, otherwise the real FastAPI app continues to write/read against the live database while the remaining tests are already correctly isolated (observed live in this session: an initial test run without `DMS_POSTGRES_DSN` created a real, harmless but unintended test row in the live `dms` database and caused an assertion against the isolated database to fail — fixed, the row was cleaned up, the test run repeated with both variables: green).
- **Live E2E via the real gateway stack**: a real PDF with a text layer from P5-S3 found via `GET /search?q=Rechnung` incl. correct ranking/snippet; search without the `X-DMS-Principal` header → `401`; a search with a principal that has no folder read permission does not return the document, and it appears after creating a role assignment on `"root"` via the real Permission Service; gateway routing enforces auth like every other service.
- **Additionally verified live since P14-S7**: two real documents uploaded via the gateway (`config-admin`, a real `document.read` role assignment on `"root"`) — prefix wildcard (`Rechnung*`) and fuzzy (typo `Rechnugnswesen~`) match the title; boolean AND/grouped OR/NOT via real HTTP calls; phrase search respects word order (correct order matches, swapped does not); proximity search (`~2`) matches; a deliberately broken query (missing parenthesis) returns `400`. Test documents were then deleted again, confirmed to have disappeared from the index.

## Open Points

- **Deep paging under heavy permission filtering is not pagination-stable**: the overfetch margin (fixed factor + hard upper bound) can, for a principal with very restricted folder permissions, make a late page appear emptier than the actually available number of hits — an SQL-side join with the Permission Service is not possible due to 3.1 (no cross-service database access); a fully stable solution would be overengineering for the current scope.
- ~~No `pg_trgm` fuzzy matching (see ADR 0012) — only exact full-text search via Postgres' stemming logic.~~ Closed in P14-S7 — see "Query Language" above and [ADR 0044](../adr/0044-search-query-language-fuzzy-proximity.md).
- **`attr.*` filters require `object_type_id`** — without a known object type, the attribute type (exact vs. range filter) cannot be resolved.
- **Folder rename does not retroactively update `folder_name`** — only on the next re-index of the respective document (an accepted inconsistency, the same pattern as other "eventually consistent, updated on next touch" cases in this system).
- **No further authorization beyond the folder read-permission check** — Search Service is the first service to evaluate the `X-DMS-Principal` header injected by the gateway at all; all remaining previously documented "Open Points" about missing authorization elsewhere in the overall system remain unchanged.
- **Proximity search is limited to exactly two words** (since P14-S7, concept wording "two terms") — a general N-word sloppy phrase (like Elasticsearch/Lucene's `"a b c"~5`) was deliberately not built, see ADR 0044.
- **Fuzzy/mixed ranking is a simple sum of independent per-leaf scores** (since P14-S7), no boolean-structure-aware `ts_rank` like in the pure non-fuzzy path — see ADR 0044 "Rationale".
- **`OR`/`NOT` are reserved, case-insensitive keywords** (since P14-S7) — the actual words "or"/"not" cannot be entered directly as search terms, the same limitation as with practically every search language that has boolean keywords.
- **No minimum length for wildcard prefixes** (since P14-S7) — a very short prefix (`a*`) can match many lexemes and is potentially slower; deliberately not artificially restricted (no concept requirement for it).
