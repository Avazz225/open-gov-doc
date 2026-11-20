# 0234 — fleet-management-service: per-operator authentication, re-confirmed and unblocked for build

**Status:** accepted — **built in P84-S4** (see "Build notes" at the end)
**Context:** P82-S1 (Phase 82, first scoping-only session — "larger remaining items" round). ADR 0212
(P73-S2) already scoped a concrete fix for `fleet-management-service`'s per-operator identity gap and
named a specific follow-up build session, **P73-S2b, which was never executed**. Per this project's own
standing rule (never rewrite an old ADR after the fact — write a new one referencing it instead), this
session does not edit ADR 0212. It re-verifies the gap and the design directly against current code, and
resolves the two decisions ADR 0212 itself left open, so a build session can proceed without its own
scoping pass.

## Re-verification (live code read, not assumed carried-over from ADR 0212)

Both cited locations are **unchanged since ADR 0212 was written** — same file, same line numbers, same
shape:

- `_require_operator_key` (`services/fleet-management-service/src/fleet_management_service/main.py:92-108`)
  is still the service's only auth primitive: one shared `Authorization: Bearer <fleet_operator_key>`
  checked with `hmac.compare_digest`, returning `None` (pass/403) — no notion of *who* called.
- `approve_installation_run` (`main.py:665`) still does `if payload.actor == run.proposed_by:` — both
  sides plain free-text `str` fields (`MarkDoneRequest.actor`/`ApprovalDecision.actor`,
  `schemas.py:152-160`), never validated against any identity.
- `docs/services/fleet-management-service.md`'s own Open Points (line 96) still lists this gap verbatim,
  unedited since ADR 0212.
- No `FleetOperator` table or equivalent exists anywhere in the codebase (grepped `services/*/src/`) —
  confirmed the design was scoped but genuinely never built.
- `bcrypt` is still absent from `fleet-management-service/pyproject.toml` — ADR 0212's "use `sha256` to
  avoid a new dependency" rationale still holds.
- The precedents ADR 0212 leaned on are both still real and unchanged: `migration-service`'s
  `paired_installation.api_key` model (opaque per-counterparty token,
  `services/migration-service/src/migration_service/main.py:513-559`) and `workflow-service`'s
  `_require_claim_authorization_if_claimed` (`main.py:1633`, "resolve identity from the verified channel,
  not the request body"). Nothing else in the codebase has since implemented a competing pattern for this
  problem that ADR 0212's design would now duplicate or conflict with.

**Verdict: the design is unchanged, still sound, and directly buildable as-is.** No re-scoping of the core
mechanism was needed — only the two items ADR 0212 itself flagged as open.

## The two decisions ADR 0212 left open, resolved here

1. **Token format**: ADR 0212 suggested UUID4 without pinning it. Confirmed as the decision — a UUID4,
   shown once at creation, hashed with `sha256` into the new `fleet_operator.token_hash` column (matching
   the already-decided no-bcrypt rationale). No prefix/namespacing needed; this service has no multi-tenant
   token-type ambiguity to disambiguate against (unlike, say, a public API key scheme).
2. **Who may mint new operators via `POST /operators`**: resolved as **admin-flagged operators only**, not
   "any valid operator." Add a boolean `is_admin` column to `fleet_operator` (default `False`); the
   bootstrap row created for the existing shared `fleet_operator_key` gets `is_admin=True`. `POST/DELETE
   /operators` require the resolved operator to have `is_admin=True`; `GET /operators` remains available to
   any authenticated operator (listing, not minting, carries no privilege-escalation risk). Rationale:
   letting *any* valid operator mint further operators would mean a single compromised operator credential
   could mint an unbounded number of new ones — the same least-privilege reasoning this project already
   applies elsewhere (e.g. domain-admin roles gate role/role-assignment creation in `permission-service`,
   not "any authenticated user"). A single admin-flagged bootstrap operator, promotable later if a real
   multi-admin need arises, is the minimal safe default and needs no new concept beyond one boolean column.

## Decision

**Recommend building ADR 0212's design in a dedicated session next**, incorporating the two resolutions
above. No implementation happens in this session (Phase 82 is scoping-only by design, matching the
ADR 0147/0161/0207/0209/0212 precedent this phase itself continues). Concretely, for the build session:

- New `fleet.fleet_operator` table: `id`, `name` (unique), `token_hash` (`sha256`), `is_admin` (bool,
  default `False`), `created_at`, `revoked_at` (nullable).
- `_require_operator_key` → `_require_fleet_operator`, resolves and returns the operator's `name` (linear
  `hmac.compare_digest` scan over active rows, same timing-safe pattern already in place).
- `mark-done`/`approve`/`reject`/`start` derive identity from the resolved operator, not from
  `payload.actor`/`started_by` — those fields drop from `MarkDoneRequest`/`ApprovalDecision`/
  `RolloutStart`. The `actor != proposed_by` comparison itself (`main.py:665`) is unchanged, only what
  feeds it becomes a verified identity instead of free text.
- Idempotent bootstrap migration: create a `"bootstrap"` `FleetOperator` row (`is_admin=True`) matching the
  existing `fleet_operator_key`, so already-deployed installations keep working unchanged until they
  choose to mint additional operators.
- New `POST /operators` (admin-only, mints a token, returned once), `GET /operators` (any operator, no
  token values returned), `DELETE /operators/{id}` (admin-only, sets `revoked_at`).
- No UI in scope (matches ADR 0212 — this remains an operator-facing, API-only concern).

## Consequences

- The build session that eventually picks this up needs no scoping work of its own — every open question
  ADR 0212 left is now resolved here.
- `docs/services/fleet-management-service.md`'s Open Points entry should be updated by that build session
  (not this one) once the fix actually lands, per this project's convention of updating docs alongside the
  code change that closes a gap, not ahead of it.

## Build notes (P84-S4)

Built as recommended, with one deliberate deviation and one addition:
- **Bootstrap stays env-backed.** Instead of hashing the env `fleet_operator_key` into the bootstrap row
  once, the env value stays the source of truth for the `bootstrap` operator: it is checked directly, and
  the row (`token_hash` empty) is only the identity. With a one-time hash, rotating the env key would
  have left the old key valid, and removing it would no longer lock that path. Both now behave as before
  this change (ADR 0172's fail-closed default). For the same reason the bootstrap row cannot be revoked
  through the API.
- **`GET /operators/me`** was added, so admin-ui can show "signed in as".
- **The removed body fields** (`actor`, `started_by`) are ignored rather than rejected when an old
  client still sends them.

Tests: 42 (+8). Details in `docs/services/fleet-management-service.md` "Operators".
