# 0188 — config-service export/compare gating, search-service facet leak fix, query-service limit clamp

**Status:** accepted
**Context:** P61-S3 (Phase 61, "Medium-Severity Findings" — third session of the sixth gap-analysis
round's live-code security sweep). Three unrelated findings, bundled into one session per the plan.

## Decision

**(a) config-service: `GET /config/export`/`POST /config/compare` gated behind a new
`admin.config_read` capability.** Both endpoints' own docstrings previously claimed they were
deliberately left ungated ("does not expose any installation-specific data") — investigation showed
that claim was wrong: `export_config` returns the full role/permission catalog, the AD-group→role
mapping table, Keycloak realm role names, and BPMN process/DMN definitions — reconnaissance-grade
information about the installation's access-control model, readable by any authenticated caller with
network access. Fixed with a dedicated READ capability (`admin.config_read`, role
`domain-admin-config-read`), distinct from the existing `admin.object_config` (a WRITE-flavored
capability already gating `POST /config/import`) — the same read/write split convention this project
already uses repeatedly (e.g. `admin.notification_read` vs `notification.write`), rather than reusing
a semantically different existing grant. `compare_config`'s docstring corrected to no longer claim it
exposes nothing installation-specific.

**(b) search-service: `facet_counts` no longer leaks across the permission boundary `search()` already
enforces.** `GET /search`'s facet counts were computed by a separate, unfiltered SQL `GROUP BY` query
over the same filters as the main search — but without the permission filtering the route handler
applies to the main `results` list via `check_batch`. A caller with no access to a folder could still
learn its name and how many matching documents it contains, purely from the facet counts. Fixed by
replacing the separate query with `facet_counts_from_readable`, a pure-Python aggregation over the
SAME, already-permission-filtered `readable` list `main.search` already builds — no separate query, no
separate leak surface.

**(c) query-service: `limit` clamped to 100 at the single shared entry point.** `GET /query/events`'s
`limit` query parameter was previously unbounded, unlike `search-service`'s own established
`limit = min(limit, 100)` precedent for the identical parameter shape (`main.py`'s `search`/
`facet_counts`). The natural-language parser-plugin path has its own `limit` extraction that fed the
same underlying call — clamping at just the `query_events` call site would have left that second path
unbounded. Fixed by moving the clamp into `_run_query`, the single function both callers share.

## Rationale

- **Why a new capability instead of reusing `admin.object_config` for (a)**: `admin.object_config`
  already means "can mutate configuration" (gates `POST /config/import`) — granting it to someone who
  should only be able to *view* the access-control catalog would over-grant write access they don't
  need. A dedicated read capability keeps the same precise read/write split this project already uses
  elsewhere.
- **Why in-process aggregation instead of a permission-filtered SQL query for (b)**: a `WHERE resource_id
  IN (...)` variant of the original `GROUP BY` query was considered, but would have needed the full set
  of readable resource IDs resolved up front anyway (the same work `check_batch` already does for
  `results`) — reusing the already-computed `readable` list avoids a second round trip to
  permission-service and a second SQL query entirely, at the cost of an accepted, honestly documented
  tradeoff: `readable` is itself bounded by `search_result_hard_limit` (the existing overfetch cap,
  unchanged by this session), so facet counts for a query whose true matching set exceeds that cap
  become an undercount — the same "eventually consistent under a cap" ceiling pagination already has,
  not a new limitation. Correct and exact for the overwhelmingly common case, and — unlike before —
  only ever describes documents the caller can actually read.
- **Why clamp inside `_run_query` instead of at each call site for (c)**: `query_events` and the
  natural-language parser-plugin path both ultimately call `_run_query` with their own independently
  derived `limit` — clamping once at the shared entry point closes both paths with one change instead
  of two, and can't be silently reopened by a future third caller of `query_events` forgetting to clamp.

## Consequences

- New role: `domain-admin-config-read` ("Konfiguration einsehen (Export/Vergleich)") in
  `permission-service`'s `DOMAIN_ADMIN_ROLES`, self-healing seed like every other domain-admin role.
- `GET /config/export`/`POST /config/compare` now `401` with no `X-DMS-Principal` header, `403` for a
  principal without `admin.config_read`, `200` as before for an authorized principal. Live-verified
  against the real running stack: all three states confirmed via `curl` (401 unauthenticated, 403 for an
  unauthorized test principal, 200 after granting `domain-admin-config-read` to a fresh test principal).
- `GET /search`'s `facet_counts` no longer reveal folder names/counts for folders the caller cannot
  read. New API-level regression test
  `test_search_facet_counts_exclude_documents_the_principal_may_not_read` (isolated folder, one
  principal granted `folder.read`, one denied, asserts the denied principal's facet counts omit the
  isolated folder's ID while the allowed principal's include it).
- `GET /query/events?limit=999999` (and the equivalent natural-language path) now fetches at most 100
  events from `audit-service`, regardless of the requested `limit`. New test
  `test_query_events_clamps_limit_to_100`. Live-verified against the real running stack: a
  `limit=999999` request against the real audit-service (100k+ events) returned
  `total_before_filter: 100`, confirming the clamp took effect before RBAC filtering.
- `services/search-service/src/search_service/repository.py`: old `facet_counts()` (two separate
  unfiltered `GROUP BY` queries) removed entirely, replaced by `facet_counts_from_readable()`; unused
  `sqlalchemy.func` import removed.
- Test suites: `config-service` 52/52 (6 new: header-default fix on two existing `/config/import` tests
  whose "without header" premise would otherwise have been silently defeated by `_client()`'s new
  default header, plus 4 new export/compare gating tests), `search-service` 76/76 (1 new), `query-service`
  54/54 (1 new), `permission-service` 182/182 (new role, unaffected otherwise). `ruff` clean across all
  four services (one pre-existing, unrelated repo-wide `ruff` failure in
  `apps/libreoffice-addin/python/ogdoc_addin.py` confirmed out of scope).
- `permission-service` rebuilt/redeployed first (new role seed), then `config-service`,
  `search-service`, `query-service`.
