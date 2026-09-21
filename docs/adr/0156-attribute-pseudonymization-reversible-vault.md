# 0156 — Reversible attribute-level pseudonymization, document-service only

**Status:** accepted (P41-S2, see Phase 41 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 41 Session 2 (Compliance gaps from the concept document), affects `document-service`,
`object-type-service` (schema convention only), `permission-service`, `admin-ui`

## Decision

Concept 5.2's entire specification for this feature is one sentence: *"GDPR tension field: deletion
obligation for personal data vs. retention obligation. Solution approach: pseudonymization/redaction of
individual personal-data attributes instead of hard-deleting the whole document, wherever a retention
obligation exists."* It uses "Pseudonymisierung" (pseudonymization) and "Schwärzung" (redaction) together,
without disambiguating - these have materially different consequences (reversible-by-design vs. one-way),
and the concept names neither a trigger mechanism, an eligible-attribute marker, nor which of document-
service/folder-service/case-service (all three independently store an `attributes` JSON column) this
applies to.

**Presented with the reversibility choice, the user selected reversible pseudonymization**: the original
value is AES-256-GCM-encrypted into a new vault table, the live `Document.attributes[name]` value is
overwritten with a fixed placeholder (`"[PSEUDONYMISIERT]"`), and an authorized admin can later decrypt
and view the original via a dedicated reveal endpoint. This resembles GDPR Art. 4(5)'s own definition of
pseudonymization more closely than one-way redaction would have, at the cost of not, by itself, fully
discharging the "Löschpflicht" (deletion obligation) the concept names - the encrypted original still
exists and remains regulated personal data for as long as the vault entry does. No automatic vault-entry
expiry/purge is built in this session (see "Consequences").

Three further decisions followed, all resolved via research/precedent without a second user question:

- **Eligibility via a new, free-form `personal_data: true` key inside the already schema-free
  `Attribute` definition** (`object-type-service`'s `attributes` list) - no object-type-service code
  change was needed, since `dms_constraint_engine` already ignores unrecognized keys during validation.
  `document-service` reads this flag via its existing `object_type_client.get()` (already used for
  `default_retention_days` etc.) before allowing a pseudonymize call - default-deny (an attribute with
  no flag, or a document with no object type at all, is never eligible), appropriate for a compliance-
  relevant action. `admin-ui`'s `ObjectTypeEditor` gained a matching per-attribute checkbox so this is
  actually reachable without raw API calls.
- **`document-service` only, this session** - `folder-service`/`case-service` independently store the
  identical `attributes` JSON shape and could, in principle, want the same mechanism, but 5.2's own
  wording ("hard-deleting the whole *document*") is document-specific, and building three near-identical
  vault/crypto/RBAC stacks in one session would have traded real depth (RBAC split, key management,
  actual encryption, thorough tests) for breadth. Mirroring to folder-service/case-service is left as an
  explicitly deferred follow-up (see "Consequences") - the same "build the primary case first" pattern
  already used elsewhere in this project (e.g. legal hold/retention landed on document-service first,
  folder-service caught up in a later session).
- **Two separate RBAC capabilities, not one** (`admin.attribute_pseudonymization` / `admin.attribute_
  reveal`, roles `domain-admin-pseudonymization` / `domain-admin-pii-reveal`) - extends the `admin.legal_
  hold` vs. `admin.deletion` split (ADR 0075) one step further: pseudonymizing REDUCES exposure of
  personal data (comparatively low-risk), revealing the original value back EXPOSES it again (materially
  higher-risk). An installation may reasonably want different people responsible for each.

## Rationale

- **Why AES-256-GCM via a duplicated, document-service-local `crypto.py` rather than a shared library**:
  `archival_service.crypto` (ADR 0029) already established the precedent for this exact algorithm/shape
  in this project, and its own docstring already contrasts itself with a THIRD, differently-shaped crypto
  module (`workflow_service.federation_crypto`, RSA-hybrid) - this codebase deliberately keeps small,
  single-purpose crypto helpers per-service rather than factoring them into a shared library. A fourth
  near-identical copy is consistent with, not a deviation from, that established pattern.
- **Why a new dedicated key (`DMS_ATTRIBUTE_PSEUDONYMIZATION_KEY`) instead of reusing archival-service's
  `DMS_ARCHIVE_ENCRYPTION_KEY`**: the two protect conceptually unrelated data (records-disposal archive
  copies vs. personal-data attribute values) in two different services with no existing trust
  relationship between their key material - reusing one key for both would couple their blast radius for
  no operational benefit. Same "no fallback to a randomly generated key" principle as `EnvKeyStore`
  either way: a random fallback would change on every restart and permanently render already-
  pseudonymized attributes unrecoverable.
- **Why the reveal endpoint doesn't restore the value in place**: the user's chosen scope was "an admin
  can later request the original back" - a transient, auditable read satisfies that literally. A
  separate "un-pseudonymize" endpoint that writes the decrypted value back into `Document.attributes`
  would be a small, mechanically obvious follow-up (the value is already in hand from `reveal`, the
  existing `PATCH /documents/{id}` attribute-update path could take it from there) - not built here since
  nothing in this session's scope required it, and deliberately not conflated with `reveal` itself (view
  and restore are different-consequence actions that deserve to stay explicit). **Built in P62-S2**:
  `POST /documents/{id}/attributes/{name}/restore` writes the decrypted value directly into
  `Document.attributes` (not via a literal `PATCH` HTTP round trip - the same repository transaction
  that already has the document loaded) and deletes the vault row, gated by the same `admin.attribute_
  reveal` capability as `reveal` itself. See `docs/services/document-service.md` "Attribute-Level
  Pseudonymization".
- **Why `reveal` is always published as an event, unconditionally** - unlike `_should_log_document_
  access`'s configurable viewed/downloaded logging (an ordinary business read), re-exposing personal data
  that was specifically pseudonymized for compliance reasons is inherently security-relevant every single
  time, not an optionally-quiet default.

## Consequences

- **New table**: `document.pseudonymized_attribute` (id, `document_id` FK, `attribute_name`,
  `encrypted_value`, `reason`, `pseudonymized_by`/`pseudonymized_at`, `last_revealed_by`/`last_revealed_
  at`). No `ALTER TABLE` needed on `Document` itself - the vault is a genuinely new table, `create_all`
  suffices.
- **New settings**: `DMS_ATTRIBUTE_PSEUDONYMIZATION_KEY` (base64, 32 bytes) on `document-service`. A
  pseudonymize/reveal call without this configured fails with `503`, not `500` - a missing installation-
  time configuration, not an unexpected server error.
- **New endpoints**: `POST /documents/{id}/attributes/{name}/pseudonymize`, `POST .../reveal`,
  `GET /documents/{id}/attributes/pseudonymized` (the last one gated like a regular document read, not
  the admin-only reveal capability - it exposes no plaintext).
- **New RBAC**: `admin.attribute_pseudonymization`/`admin.attribute_reveal` (roles `domain-admin-
  pseudonymization`/`domain-admin-pii-reveal`), auto-seeded on every fresh installation via
  `ensure_domain_admin_roles` like every other domain-admin role in this project.
- ~~**Encrypted vault entries have no expiry or automatic purge** - the concept's own "deletion obligation"
  is only partially discharged by this session's mechanism as a result: the pseudonymized value is no
  longer live/searchable, but the encrypted original persists in the vault indefinitely, still
  personal data under GDPR for as long as it exists. A future session would need to decide a genuine
  retention policy for vault entries themselves (e.g. hard-delete after N years, or on the same
  retention/legal-hold machinery `document-service` already has) before this fully closes the concept's
  stated tension - explicitly NOT solved by this session.~~ — **closed in Phase 52 Session 3**
  ([ADR 0169](0169-pseudonymization-vault-retention-tied-to-document-lifecycle.md)): a vault entry is
  now deleted automatically the moment its own document is hard-deleted (forced deletion, trash-expiry
  purge, or records-quarantine auto-delete), tied to exactly the retention/legal-hold machinery this
  paragraph itself named as the intended option. Also fixed a real, latent FK-violation bug found while
  building this: `hard_delete_document` never cleaned up vault rows before, and their FK to `Document.id`
  has no `ondelete=` clause.
- ~~**No automatic trigger on retention expiry** - only a manual, admin-invoked action this session. The
  concept's own sibling section (5.2a) explicitly permits a purely manual/direct-configuration mechanism
  ("BPMN-modelable OR direct object/object-type configuration"), so this is a legitimate, deliberately
  narrower scope, not a gap accidentally left open - but a genuinely complete implementation of the
  concept's framing ("instead of hard-deleting... wherever a retention obligation exists") would
  eventually want an automatic path too (e.g. a new per-object-type/per-document flag, checked in
  `document_service.main._retention_poll_loop` alongside the existing soft-delete/forced-deletion
  branches). Deferred as an explicit Open Point, not built speculatively.~~ **Note (Phase 52 Session 3)**:
  this bullet is about triggering pseudonymization itself automatically (still not built, still open) -
  a DIFFERENT question from vault-entry retention/purge above, which ADR 0169 does now close. —
  **closed in Phase 58 Session 1** ([ADR 0177](0177-pseudonymization-retention-trigger-and-folder-case-mirroring.md)):
  `document-service`'s new `retention_pseudonymize` flag, checked in `_retention_poll_loop` exactly as
  this bullet itself anticipated.
- ~~**`folder-service`/`case-service` have no equivalent mechanism** - their own `attributes` JSON columns
  remain unaffected by this session. A future mirroring session would reuse the same vault-table/crypto/
  RBAC shape.~~ — **closed in Phase 58 Session 1** ([ADR 0177](0177-pseudonymization-retention-trigger-and-folder-case-mirroring.md)):
  `folder-service` got the full mirror including its own automatic retention-expiry trigger (it already
  has the same `retention_until`/`full_deletion`/poll-loop shape as `document-service`); `case-service`
  got a manual-only mirror (no auto-trigger, since it has no retention concept at all to hook one into) -
  both reusing the exact vault-table/crypto/RBAC shape this bullet itself named.
- **Tests**: `services/document-service/tests/test_attribute_pseudonymization.py` (new, 13 tests) covers
  RBAC (401/403 split between the two capabilities), eligibility (400 for a non-personal_data attribute,
  an unknown attribute, and a missing value), the full pseudonymize→reveal round trip (value integrity,
  live placeholder, no live restore), double-pseudonymize `409`, and listing. `apps/admin-ui/tests/
  object-type-editor.test.tsx` (+2) covers the new checkbox on create and on load-for-edit. Existing
  suites unaffected: `document-service` 373 tests (previously 360), `permission-service` 181 tests
  (unchanged count, new roles read dynamically from `DOMAIN_ADMIN_ROLES`), `admin-ui` 249 tests
  (previously 247).
