# 0031 — Diagnostic query console: pglast as an optional plugin instead of a bundled GPL-3.0 dependency

**Status:** accepted
**Context:** P8-S0 (phase kickoff planning for Phase 8, Concept 6.1 "Central Query & Trace Console"/6.2 "CLI Tool"), `IMPLEMENTATION_PLAN.md` explicitly named checking the state and license of the `pglast`/`libpg_query` library for this session, before P8-S1. A pure research/check-in session, no implementation code.

## Decision

Concept section 1a/6.1 names `pglast` as the "decided" library for the query console's language implementation (Python binding to the real PostgreSQL parser via `libpg_query`, including AST visitor/printer infrastructure for extending it with DMS-specific trace/hierarchy operators). The actual check at session start found: **`pglast` is licensed GPL-3.0-or-later** (confirmed via PyPI metadata, current version 8.4 from 2026-07-22, actively maintained, PostgreSQL 18 support in the latest branch, PG16 — the version used here, see `infra/docker-compose.yml` — covered via the older `v6` branch). `libpg_query` itself (the underlying C library) is, by contrast, **BSD-3-Clause**/PostgreSQL License, i.e. unproblematic — the license conflict arises solely from `pglast`'s own license choice for the Python wrapper.

Alternatives checked for the Python binding: **`pgparse`** (BSD-3-Clause, on PyPI, but only 74 commits/2 stars — a very small, immature project that, per its own documentation, offers only parsing/normalization/fingerprinting, no AST manipulation or prettifier infrastructure) and **`psqlparse`** (BSD, but effectively abandoned — last stable release 2016, last pre-release 2019). Neither is a full substitute for `pglast`'s visitor/printer capabilities, which 6.1 explicitly needs for the grammar extension.

**Decided via `AskUserQuestion` at session start**: the future Query & Trace Service (P8-S1) gets **the same plugin architecture as KDBX in [ADR 0029](0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)** — the core defines a thin parser-extension interface, `pglast` itself is **not** bundled into the Docker image by default, but documented and shipped as an optional plugin the operator installs themselves.

## Rationale

- **Same GPL-3.0 argument as ADR 0029**: unmodified use as a dependency is not exempted from the GPL-3.0 copyleft obligation (unlike LGPL, see ADR 0018) — a bundled `pglast` in the self-contained Docker image would subject the Query & Trace Service (and, depending on interpretation, distributed overall images) to the GPL.
- **The alternatives checked are not a real option**: `pgparse` is too immature (74 commits, 2 stars, no recognizable widespread use) and offers only parsing/normalization without AST manipulation/prettifier — exactly the capability that 6.1 explicitly requires for the DMS-specific additional constructs (trace/hierarchy operators) via `pglast`'s visitor/printer pattern. `psqlparse` is effectively dead.
- **Plugin approach instead of switching libraries or a custom implementation**: the plugin approach preserves the full `pglast` functionality actually "decided" in the concept, instead of replacing it with a functionally weaker library or writing a custom SQL parser — the latter is explicitly ruled out by 6.1 itself ("deliberately no custom parser written from scratch").
- **Consistent with the already-established plugin principle** for storage backends (ADR 0017) and KDBX (ADR 0029): operators who want to use the query console's full psql-like manipulation capability deliberately install `pglast` themselves in their own environment/image build — exactly the case that GPL-3.0 permits without restriction, since the copyleft obligation then applies only to their own, non-redistributed operation.
- **This assessment is not legal advice** (same caveat as ADR 0018/0029): a pragmatic, technical evaluation for the current development phase. Before any third-party distribution of the overall system, the license question must be reviewed again.

## Consequences

- P8-S1 (Query & Trace Service) designs a plugin interface for the parser/grammar extension instead of importing `pglast` directly; the Query & Trace Service's standard Docker image remains GPL-free.
- Without the `pglast` plugin installed, the service offers only limited functionality — the exact scope of a sensible minimal operation without the plugin (e.g. purely structured read-model queries without the full psql dialect) is to be determined at P8-S1 session start; not a technical obstacle, but deliberately deferred here.
- `libpg_query` itself remains unproblematic (BSD/PostgreSQL License) and could also be bound directly if needed — only `pglast`'s own Python wrapper is the source of the license problem.
- Anyone wanting the query console's full manipulation capability (6.1) must add the `pglast` plugin themselves in their own environment — to be documented accordingly (`docs/services/<query-service>.md`, operations documentation), analogous to KDBX.
- Larger installations that instead want to use a custom parser or a commercial solution are unaffected by this license question regardless.
