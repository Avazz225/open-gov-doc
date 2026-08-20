# 0113 — Draft / pre-registration lifecycle for documents and cases

**Status:** accepted (P31-S2, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 2 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md)), affects `document-service`,
`case-service`

## Decision

Both `document-service` (`POST /documents`) and `case-service` (`POST /cases`) get a new, optional
`draft: bool = False` flag at creation time. A draft is created exactly like a regular object, except
its reference-number generator call is skipped entirely: `document-service` never calls
`ObjectTypeClient.next_kennzeichen()`, `case-service` never calls `next_vorgangsnummer()`. Both models
get a new nullable `registered_at: datetime | None` column — `None` while a draft, set once a new
`POST .../{id}/register` endpoint is called (or immediately at creation time for a regular,
non-draft object). Registration is one-way: there is no "un-register", and a second `register` call on
an already-registered object returns `409`.

## Rationale

- **Why a dedicated `registered_at` marker, not just "does `Kennzeichen`/`vorgangsnummer` exist"**: for
  `document-service` this would work (the attribute key is simply absent), but it would conflate two
  different meanings on `case-service`'s `Case.vorgangsnummer` — that field was already nullable before
  this session, for the *unrelated* reason that it was only introduced in P15-S3 and never backfilled
  onto older rows. `vorgangsnummer IS NULL` on its own can't distinguish "predates the numbering scheme
  entirely" from "a genuine new-style draft, deliberately not yet registered" — a dedicated,
  purpose-built column can. `document-service` gets the same dedicated column for symmetry and because
  it reads more directly at every call site than re-deriving the state from attribute-key presence.
- **Backfill only at first-add, not on every startup**: like several ad-hoc migrations before it (see
  `main.py`'s `object_type_id` drift-correction comment, P15-S1), a plain unconditional
  `UPDATE ... SET registered_at = created_at WHERE registered_at IS NULL` would look idempotent but
  isn't — on every later restart it would silently re-stamp a real, still-unregistered draft as
  registered. Both migrations therefore check `information_schema.columns` first and only run the
  one-time backfill (marking every pre-existing row as already registered, which is correct: none of
  them are the new kind of deliberate draft) exactly once, at the moment the column is actually added.
- **No `kennzeichen_admin_role` gate on `POST /documents/{id}/register`**: the existing PATCH-time gate
  (P5e-S2) protects against *overriding* an already-assigned reference number. Assigning one for the
  first time via the intended registration action is a different, ungated, everyday action — consistent
  with `POST /documents` itself never having required that role for the at-creation-time assignment
  either.
- **`case-service`'s register endpoint keeps the existing `case.write` permission check**
  (`_require_case_permission`), unlike `document-service`'s (which has no permission check on its core
  document CRUD at all today, a pre-existing, documented gap this session doesn't newly introduce or
  close) — each service's register endpoint simply matches whatever authorization its own create
  endpoint already has, rather than inventing a new authorization tier specific to registration.
- **`document-service`'s register endpoint can return `422`** (`MissingKennzeichenAttributeError`) if the
  object type's `kennzeichen_format` references a placeholder attribute the draft was never given a
  value for — the identical failure mode `POST /documents` already has for a non-draft document, just
  deferred to registration time. `case-service`'s register endpoint cannot fail this way:
  `next_vorgangsnummer()` is a single installation-wide, always-configured generator (`CaseNumberConfig`
  has a default format), with no per-object-type "not configured"/"missing placeholder" states.
- **Nothing new needed to keep drafts out of anything that assumes a real reference number** — verified
  against the actual consumers rather than assumed: `document-service.list_documents_by_kennzeichen`
  and `case-service.list_cases_by_vorgangsnummer` already match on `Document.attributes["Kennzeichen"]`/
  `Case.vorgangsnummer` values, so a draft (which has neither) is naturally never matched;
  `mail-connector`'s Kennzeichen/Vorgangsnummer-based inbound-mail matching inherits the same exclusion
  for free. No new filter parameter was added to `GET /documents`/`GET /cases` — out of this session's
  stated scope, nothing in the codebase currently needs to list-and-exclude drafts as a distinct query.

## Consequences

- A draft document/case is fully functional otherwise — versioning, folder placement, attributes,
  workflow instances (for cases) all work identically to a registered one. Only the reference number is
  deferred.
- `attributes["Kennzeichen"]` can still be set on a draft via `PATCH /documents/{id}` with the
  `kennzeichen_admin_role` (the existing gate still applies unchanged) — an admin manually pre-assigning
  a value bypasses `register`'s generator call entirely, exactly as already possible for a non-draft
  document before this session. `register` itself does not check whether `Kennzeichen` is already set
  this way; it always calls the generator and overwrites, since `registered_at IS NULL` is definitionally
  "generator not yet run" regardless of what the attribute currently holds.
- No UI surfaces this for `case-service`: there is no dedicated case-creation frontend anywhere in the
  project today (cases are created via direct API calls with a `process_definition_id`) — this session's
  frontend work (draft toggle, "Draft" badge, "Register" action) is `document-service`/`user-ui` only.
- A future session could add `registered: bool` query filters to `GET /documents`/`GET /cases` if a real
  consumer needs to list drafts separately — not built here, no concrete need identified yet.
