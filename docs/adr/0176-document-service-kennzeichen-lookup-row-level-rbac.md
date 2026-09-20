# 0176 — Row-level RBAC filtering for `GET /documents/by-kennzeichen`

**Status:** accepted
**Context:** P56-S2 (Phase 56, "RBAC / Maintenance-Mode Completion" — second and last session of the
fifth gap-analysis round's third phase). ADR 0149 itself, when it retrofitted row-level RBAC filtering
onto every other cross-folder read path in `document-service`, explicitly named `GET /documents/
by-kennzeichen` as "a separate, larger effort, out of scope" for that session. This session closes it.

`GET /documents/by-kennzeichen` (2.5/3.3, P15-S3, `mail-connector`'s candidate-matching path) had **no
permission check of any kind** — no `X-DMS-Principal` requirement, no row-level filtering. Unlike `GET
/documents?folder_id=...` (already scoped to one, already-permission-checked folder), this is a genuine
cross-folder, installation-wide search — a caller could enumerate the existence and metadata (title,
object type, attributes) of any document, including one they have no read access to at all (e.g. a
teamspace-isolated document, `inherit=False`, ADR 0149), simply by guessing or brute-forcing Kennzeichen
values. "Everyone"'s baseline `document.read` grant (ADR 0149) covers the large majority of documents by
default, but not the ones this gap specifically exposed.

## Decision

**Require `X-DMS-Principal` and row-level filter the candidate list via `check_read_batch`, the same
already-existing batched permission check this service's own `PermissionServiceClient` gained in Phase 50
Session 4** — no new client method needed. `401` with no header (matching `_require_document_permission`'s
own established convention); otherwise fetch every Kennzeichen match first (as before), then a single
`POST /check/batch` call against `permission-service` (one round trip regardless of candidate count —
already closes the exact "unbounded per-candidate fan-out" shape this project has hit and fixed before in
`reporting-service`/`query-service`'s own `filtering.py`), keeping only documents the caller can actually
read.

## Rationale

- **Why `check_read_batch` and not a new `filtering.py` module**: `document-service` already has this
  exact batched-check primitive (`PermissionServiceClient.check_read_batch`, built for `webdav-connector`'s
  own N+1 fix in Phase 50 Session 4) — reusing it here is a one-call addition, not new infrastructure. A
  dedicated `filtering.py` (the pattern `reporting-service`/`query-service` use) exists there because those
  services filter heterogeneous, cross-service event streams needing a resource-ID *resolution* step first;
  here, every candidate is already a `document-service`-local `Document` row whose own `id` **is** its
  `resource_id` directly (ADR 0154) — no resolution step needed, so the extra module would add nothing.
- **Why no superuser bypass**: `document-service` has no activated-superuser concept anywhere in its own
  code (confirmed via grep — the concept exists in `auth-service`/`reporting-service`/`query-service`, not
  here). Not invented for this one endpoint; every other permission check in this service has the same
  shape.
- **Why `mail-connector` (the actual primary caller) needed no client-side change**: its
  `DocumentClient._SYSTEM_PRINCIPAL_HEADERS = {"X-DMS-Principal": "mail-connector"}` is already applied to
  every request the client makes, set once at `httpx.AsyncClient` construction (Phase 38 Session 4) — this
  endpoint's own call site never had a per-call header override skipping it, so the fixed system identity
  was already being sent; only this session's server-side check was missing. `"mail-connector"` as a
  principal is granted the same "everyone" baseline `document.read`, so its automated candidate-matching
  behavior for ordinary (non-teamspace-isolated) documents is unaffected — a teamspace-isolated document
  now correctly stops appearing as a match candidate for it too, which is the intended tightening, not a
  regression (an automated match proposal referencing a document nobody in the mail's context could ever
  actually open would have been a dead end regardless).

## Consequences

- `GET /documents/by-kennzeichen` now returns `401` without `X-DMS-Principal`, and silently narrows its
  result set to only documents the caller can read — a caller who previously got 0 candidates because none
  matched now also gets 0 candidates if matches exist but are unreadable, same as every other row-level-
  filtered list endpoint in this project (no distinguishing "hidden" from "genuinely absent," the
  established convention for this class of check).
- New regression tests prove: a caller with baseline read access still sees an ordinary match, a caller
  without read access to a teamspace-isolated match does not see it, and no principal header returns `401`.
- Live-verified against the real running stack: a real teamspace-isolated document with a Kennzeichen no
  longer appears in a non-member's `by-kennzeichen` lookup, while it still appears for an actual member and
  for `mail-connector`'s own real, unchanged automated matching flow.
