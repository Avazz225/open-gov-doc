# 0210 — Two Konzept findings (6.1 query language, 3.5 BFF layer): scoping and reconciliation

**Status:** accepted
**Context:** P72-S4 (Phase 72, last session, **scoping-only** — see `IMPLEMENTATION_PLAN.md` "Phase 72").
Two independent Konzept findings whose realistic build path was genuinely unclear, scoped together
since both are "is this actually worth pursuing" questions rather than "how do we build it" questions:
**6.1**'s free-form psql-syntax query language (never shipped after `pglast` turned out GPL-licensed,
ADR 0031) and **3.5**'s per-UI backend-for-frontend/aggregation layer (conflated with the already-built
API gateway from ADR 0005 onward, never separately implemented).

## Finding 1 (Konzept 6.1): decline to build — no demonstrated operator need, and the real blocker
isn't the parser

**What already exists**: `query-service` ships the curated, hardcoded structured filter/manipulation
catalog ADR 0031 always intended as the interim (`manipulation.py`), with row-level RBAC filtering, dry
run, and the full four-eyes/mandatory-critical-table security model Konzept 6.1 itself specifies —
everything EXCEPT free-form psql-syntax input parsing. `ParserPlugin` (`query_service/parser.py`)
already exists as the extension point ADR 0031 designed for this, unused by any shipped plugin.

**A non-GPL alternative to pglast does exist today**: `sqlglot` (MIT-licensed, actively maintained) can
parse SQL-like syntax into an AST without needing a live Postgres connection or executing anything — a
better sandboxing fit for this use case than pglast's execution-adjacent tooling, though it is a general
SQL transpiler rather than PostgreSQL's own grammar, so full psql-dialect fidelity would be weaker than
the concept's original "real PG parser" ambition.

**Recommend NOT building this, even with a viable non-GPL library now available**, for a reason
independent of licensing: `docs/services/query-service.md`'s own Open Points already name the real
remaining blocker as "a true filter-based SQL manipulation system would need new bulk endpoints in
several owner services" — a substantially larger undertaking than the parser choice itself, and one with
no documented operator demand anywhere across this project's history. ADR 0031 itself already frames the
gap as "not a technical obstacle, but deliberately deferred." Finding a parser library doesn't change
that the deferred capability has never caused a felt gap in ~72 phases of real usage — reopening it now
would be building capability nobody has asked for, not closing a gap someone hit.

## Finding 2 (Konzept 3.5): decline to build — the gateway's plain-proxy shape has never caused a
problem worth solving

**What already exists**: `gateway-service` is confirmed a pure reverse proxy — one route
(`/api/{service_type}/{path}`), one backend call per frontend call, zero multi-service aggregation
anywhere in its source. Where a UI screen genuinely needs two related pieces of data, aggregation
already happens client-side via trivial 2-call `Promise.all` patterns in the relevant component (e.g.
`user-ui`'s `ExplorerPane` combining deleted-documents/deleted-folders for one trash view) — not an N+1
chain, not a documented performance problem anywhere in this project's history.

**Recommend NOT building a separate BFF layer.** `docs/services/gateway-service.md`'s Open Points are
extensive after 200+ ADRs of iteration on this exact service, yet never once name missing
aggregation/BFF as a gap — that silence, combined with zero documented multi-call performance pain
across every frontend app, is strong evidence this was never a felt requirement, only aspirational
phrasing that got absorbed into the gateway's own title (ADR 0005 is literally titled "...API
gateway/BFF...") without the BFF half ever being a distinct, separately-motivated requirement.

## Decision: leave `Konzept.md` itself unedited; the reconciliation lives in the ADR record instead

This session's own Definition of Done left open the possibility of correcting `Konzept.md`'s text
directly if a finding concluded the concept itself was stale rather than the implementation incomplete —
both findings above reach exactly that conclusion. This session nonetheless recommends **not** editing
`Konzept.md`, for consistency with this project's own established, unbroken practice across every prior
phase: `Konzept.md` has functioned throughout this project's entire history as the fixed, original
reference baseline that ADRs and `docs/services/*.md` files cross-reference and reconcile AGAINST, never
a document that itself gets retroactively rewritten to match what was built. Editing it now — even
narrowly, even with good reason — would be the first departure from that convention in 72+ phases, and
this session found no evidence that convention has caused any real confusion worth breaking it over
(both gaps were already correctly, findably documented in ADR 0031 and the two services' own Open
Points before this session started). The reconciliation this session performs is recorded here and
cross-referenced from both services' docs instead — the same mechanism every other Konzept-vs-reality
gap in this project has always used.

## Consequences

- **No code diff, and deliberately no `Konzept.md` diff either** — both findings conclude "decline to
  build," and this session's own reasoning above explains why the concept text itself stays untouched
  rather than being revised, a conscious choice, not an oversight.
- **Both gaps remain named exactly where they already were** (ADR 0031; `docs/services/query-service.md`
  and `docs/services/gateway-service.md`'s own Open Points) — this ADR adds the explicit "considered and
  declined, here's the current non-GPL-library landscape, still no operator need" record so a future
  gap-analysis round doesn't have to re-derive the same conclusion from scratch a third time.
- **This concludes Phase 72** ("Scoping-Only Sessions for Larger Candidates") in full (4/4 sessions:
  ADR 0209/0207/0208/this one) — `graphify update .` runs once at phase end per the standing rule.
- **What would change either conclusion**: for 6.1, a real operator request for ad-hoc diagnostic
  querying beyond the existing structured catalog, at which point the bulk-endpoints question (not the
  parser question) is the one to scope first; for 3.5, a documented multi-call performance problem in a
  real frontend screen, at which point a narrow, screen-specific aggregation endpoint would likely be a
  smaller, more targeted fix than a general BFF layer anyway.
