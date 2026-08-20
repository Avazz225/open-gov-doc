# 0114 — Classification level becomes a genuine per-document/per-version attribute

**Status:** accepted (P31-S3, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 3 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md)), affects `document-service`,
`permission-service`

## Decision

`classification_level` ("VS-NfD"/"VS-VERTRAULICH"/"GEHEIM"/"STRENG GEHEIM", 14.2) previously lived only on
`ObjectType` (object-type-service) — a fixed, class-wide default, never a property of an actual document.
This session adds `Document.classification_level` (nullable) and `DocumentVersion.classification_level`
(a snapshot, taken at check-in time) to `document-service`. At document creation, the object type's own
`classification_level` (if any) is copied once as a seed — the exact same pattern already used for
`retention_until`/`archive_after` from `ObjectType.default_retention_days`/`default_archive_after_days`.
From then on the document's own field is authoritative and independently changeable via a new
`PUT /documents/{id}/classification-level` endpoint, gated by a new dedicated `admin.classification`
capability (role `domain-admin-classification`) — separate from `admin.object_config`, which continues to
govern the object type's own default/seed value.

## Rationale

- **Set-or-raise only, never lower via this endpoint**: `repository.set_classification_level` compares
  rank order (`None` < VS-NfD < VS-VERTRAULICH < GEHEIM < STRENG GEHEIM) and rejects a strictly lower
  target with `409`. Setting the *same* level again is treated as an idempotent no-op, not an error. A
  request to clear a document back to unclassified is not offered by this endpoint **at all** — matching
  the plan's literal wording ("who may set or raise it"), and matching real-world handling of these
  classification levels, where declassification is a separate, heavier administrative process, not a
  routine action a single role should be able to perform via a plain "set the field" call. No such process
  is built in this session — a genuine gap if ever needed, deliberately left open rather than
  under-designed here.
- **A dedicated NEW capability (`admin.classification`), not `admin.object_config`**: setting a specific
  document's actual classification is materially more sensitive than editing object-type schemas in
  general (which anyone with `admin.object_config` can already do today, including creating/removing the
  classification levels object types default to). Reusing that broader capability for this narrower,
  higher-stakes action would either over-grant it to every config admin or force every classification
  change through the same door as routine schema editing. Mirrors the existing precedent of
  `domain-admin-legal-hold` being kept separate from `domain-admin-deletion` for the analogous reason (ADR
  0075): two conceptually distinct sensitive actions get two distinct domains, not one shared one.
- **The newer `permission-service` capability pattern (`has_permission()`), not the older `X-DMS-Roles`
  string-match pattern**: research into this codebase's own history found that every dedicated role added
  since Phase 19 (`admin.legal_hold`, ADR 0075; `admin.quarantine`, ADR 0073 — which explicitly frames
  itself as replacing an older `X-DMS-Roles` gate) uses this pattern, while the older
  `kennzeichen_admin_role`/`trash_hard_delete_admin_role`/`classified_trash_hard_delete_admin_role`/
  `quarantine_release_admin_role` settings predate it and were never migrated. A brand-new role in Phase 31
  has no reason to extend the legacy pattern instead of the one this project has consistently used for
  every dedicated role since. The pre-existing `trash_hard_delete_admin_role`/
  `classified_trash_hard_delete_admin_role` settings (who may access/purge each trash view) are
  **unchanged** by this session — a separate concern from who may set a document's classification, out of
  scope here.
- **Per-version snapshot, not just a per-document field**: the plan explicitly says "per-document,
  per-version". `DocumentVersion.classification_level` is set once at check-in time from the *document's
  current* value (not re-derived from the object type) and is never retroactively rewritten by a later
  raise on the document — the same "history doesn't silently change when current state changes" principle
  already established for case-service's closure snapshot (`snapshot_version_number`, 2.3). A version
  checked in while the document was VS-NfD keeps showing VS-NfD even after the document is later raised to
  GEHEIM.
- **The object-type-level field and its `applies_to=="document"`-only validation are unchanged** — it
  remains exactly what it already was: a per-class default/seed and the basis for `ObjectTypeEditor`'s
  classification dropdown. This session does not touch `object-type-service` at all.
- **The classified-documents-trash and manual-purge gates now read the document's own field directly,
  removing a live HTTP round trip to object-type-service on every trash-listing/purge call** —
  `list_classified_document_type_ids()` (a per-request `GET /object-types?...&is_classified=true` call)
  is deleted as dead code; `list_deleted_documents` gained a plain `classified: bool | None` parameter
  filtering on `Document.classification_level IS (NOT) NULL` instead of an object-type-ID `IN`/`NOT IN`
  set. A genuine simplification enabled by the field now existing where it's actually needed, not a
  deliberate architecture change beyond what per-document classification already implies.

## Consequences

- No migration risk: both new columns are purely additive and nullable; every pre-existing row correctly
  reads as unclassified (`NULL`) — unlike `registered_at` in ADR 0113, no backfill is needed or performed
  here, since `NULL` was already the semantically correct value for every row before this session.
- A document's classification is now independent of its object type after creation — retyping a document
  (not itself supported today) or a later change to the object type's own default has no retroactive
  effect on already-created documents, exactly like retention/archive-after.
- Frontend (`user-ui`): `MetadataPanel` shows the current level (read-only for everyone) and, for
  principals with `admin.classification`, a raise action restricted to ranks at or above the current one.
  Version history shows each version's own snapshot as a small badge — the first genuinely new
  per-version-only display in this codebase (previously only intrinsic, immutable version metadata was
  ever shown per version).
- A future session could wire up the already-seeded-but-dormant `admin.deletion_classified` capability
  (found unused during this session's research — a "Löschadministration (Verschlusssachen)" domain that
  exists in `permission-service`'s seed list but nothing anywhere calls `has_permission` for it) to finally
  replace `classified_trash_hard_delete_admin_role`'s legacy string-role gate with the newer pattern too —
  not built here, a distinct concern (who may purge vs. who may classify) from this session's scope.
