# 0116 — Records quarantine: a fourth, independent lifecycle axis

**Status:** accepted (P31-S5, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 5 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md)), affects `document-service`,
`permission-service`

## Decision

A document can be moved into records quarantine — an administered holding area with restricted visibility
and an optional, configurable auto-delete condition — via a new `RecordsQuarantine` table (one row per
quarantine period, same shape as `LegalHold`) and three endpoints (`POST /records-quarantine`, `.../
{id}/release`, `GET /records-quarantine`). While active (`released_at IS NULL`), the document disappears
from `repository.list_documents_by_folder` (and therefore from folder export, which reuses it); if
`auto_delete_at` is set and reached, `_retention_poll_loop`'s new fourth phase permanently deletes the
document unless an active `LegalHold` on it blocks that, exactly like the loop's three existing phases.

## Rationale

- **A fourth axis, not a variant of an existing one** — verified against every existing document lifecycle
  mechanism before designing this one:
  - **Not Legal Hold** (ADR 0075): legal hold *prevents* deletion indefinitely and does **not** restrict
    visibility (a held document stays fully visible in normal folder listings) — the opposite pairing from
    what quarantine needs (hide now, *enable* scheduled destruction later).
  - **Not the virus-scan quarantine** (ADR 0052/0073): that holds bytes that never became a `Document` row
    at all, lives in a completely different service (`virus-scan-service`), and exists for malware
    isolation, not records management. Deliberately named "records quarantine" throughout (never just
    "quarantine") to keep the two unmistakably distinct in code, docs, and UI.
  - **Not the archival/disposal pipeline** (P7-S3/P7-S3b): that *converts and relocates* content to
    long-term archive storage while deliberately keeping the `Document` metadata row visible/findable —
    the opposite of "restricted visibility" — then dehydrates (removes) only the live copy. Records
    quarantine does not touch storage format/location at all.
  - **Not `retention_until`/`full_deletion`**: the regular retention schedule is a single, always-present
    per-document field pair; quarantine is an optional, separately-triggered, separately-audited holding
    period layered on top, with its own row-based history (who quarantined it, when, why — same rationale
    `LegalHold`'s own docstring gives for being a table rather than a field: "the history... is itself
    audit-relevant").
- **RBAC: reuse the *pattern*, not the literal `admin.legal_hold` capability** — a new
  `admin.records_quarantine` capability and a new `domain-admin-records-quarantine` role, checked via the
  identical `_require_<x>_permission` → `permission_client.has_permission(...)` shape as
  `_require_legal_hold_permission`. This reading is not a guess: ADR 0075 itself explains *why* legal hold
  got its own domain instead of reusing the pre-existing `domain-admin-deletion` ("a legal hold PREVENTS
  deletion, a deletion administrator PERFORMS it — opposing responsibilities... merging them would have
  forced an installation to grant either both or neither, without separation of duties"), and ADR 0114
  later cites this exact precedent by name when adding classification's own dedicated domain. The same
  reasoning applies here even more directly: legal hold protects records, records quarantine schedules
  their destruction — textbook opposing responsibilities, the strongest possible case for a separate,
  independently-grantable capability rather than folding into `admin.legal_hold`.
- **`GET /records-quarantine` is gated, unlike `GET /legal-holds`** — a deliberate, documented divergence
  from the otherwise-mirrored Legal Hold pattern. Legal hold's status is not itself sensitive (anyone
  viewing a document can already see it's held); records quarantine's very purpose is to hide documents
  from normal view, so listing what's currently quarantined is exactly the restricted content the feature
  exists to protect, and must be gated by the same capability as setting/releasing it.
- **Poll-loop integration reuses the existing `_retention_poll_loop`** rather than starting a second
  asyncio loop: the loop's own docstring already frames itself as "poll instead of a real BPMN process,"
  the same rationale that applies to quarantine's auto-delete condition. Legal hold's own gate
  (`has_active_hold`) is already implemented as a query-time filter inside the relevant repository
  functions, not a separate loop or an `if held: skip` branch inline in the loop body — quarantine's new
  `list_expired_quarantine` mirrors that exact shape (candidates filtered by due `auto_delete_at`, then by
  the *document's* `has_active_hold`).
- **Visibility restriction implemented as a `NOT EXISTS` subquery, not the N+1 `has_active_hold`-per-
  candidate pattern** used elsewhere in this file. Those call sites (`list_due_for_reminder`, `list_due_
  for_retention_action`, `list_expired_trash`) all run against a small, date-filtered candidate set inside
  a low-frequency poll loop — acceptable there. `list_documents_by_folder` is a hot path hit on every
  folder navigation, so `list_documents_by_folder`'s new exclusion is a single `NOT EXISTS` subquery
  instead, keeping it a single query regardless of how many documents are quarantined.
- **`hard_delete_document` also removes the (now-terminal) `RecordsQuarantine` row itself** as part of its
  existing dependent-row cleanup (mirroring how it already removes `LegalHold` history) — no separate
  "release the quarantine" step is needed before or during auto-delete; the row simply goes with the
  document it referenced.

## Consequences

- A document can be simultaneously quarantined, legally held, and/or sitting in the regular trash — all
  three are independent, composable states, exactly like legal hold today already coexists with the
  regular trash. No mutual-exclusion validation was added between them; adding one is not blocked by the
  others being present.
- **Deliberately not part of this session**: `search-service` is NOT made quarantine-aware. A quarantined
  document remains fully findable via search, which is a real, honestly-acknowledged visibility gap
  relative to the folder-browsing experience. This was a deliberate scope decision, not an oversight: the
  two structurally safe ways to close it both had real problems on inspection. Reusing the already-
  recognized `document.deleted` event to trigger a search-index removal would have made `audit-service`
  (which records every `document.>` event verbatim) permanently and incorrectly show the document as
  *deleted* at the quarantine timestamp — an audit-trail correctness bug, not an acceptable trade-off.
  Building a genuinely new, correctly-named event type risks the exact pitfall found live during P31-S4
  (ADR 0115): `rendering-service`'s (and, by the same pattern, other consumers') dispatchers match specific
  event-type strings exactly, so a new type silently reaches nothing unless every relevant consumer is
  updated in lockstep. Properly closing this gap needs its own session, once a clear owner-consumer story
  for a genuinely new event type (or a `search-service`-side per-result quarantine check) is worked out.
- **`case-service` is untouched** — it has no destruction-scheduling primitive of any kind today (only
  `status` open/closed and the archival-only `archive_after`/`archived_at`), so there is no existing hook
  point to layer a quarantine gate onto, unlike document-service's already-existing `_retention_poll_loop`.
  Building that primitive from scratch for cases would be a materially larger, separate session — the same
  scoping conclusion this project already reached for redaction (ADR 0115) when case-service turned out to
  have no realistic entry point either.
- No new admin-ui/user-ui surface was required to *view* the quarantine holding area cross-folder in this
  session (see `docs/services/user-ui.md` for exactly what was built) — a dedicated cross-document
  "records-quarantine holding area" browser, analogous to `TrashPane`'s `admin`/`admin_classified` scopes,
  is a reasonable follow-up but not required for the mechanism itself to work correctly.
