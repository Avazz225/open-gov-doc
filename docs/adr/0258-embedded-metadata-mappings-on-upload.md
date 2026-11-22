# 0258 — Embedded file metadata fills document attributes on upload

**Status:** accepted
**Context:** P90-S5 (Phase 90, user-selected from the comparison with an external reference system).

Office and PDF files carry their own properties: title, author, subject, keywords, creation date. Users
already typed them once in Word or their PDF tool, and typing them again as document attributes is
the kind of work an upload should save. The external reference system copies such properties into
its index fields, configured per document class.

## Decision 1 — Mappings live on the object type

A document type gets `metadata_mappings`, a list of
`{source, attribute, overwrite}`.

- **`source`** comes from one fixed vocabulary shared by every format: `title`, `subject`, `author`,
  `keywords`, `description`, `category`, `language`, `created`, `modified`, `last_modified_by`,
  `application`. An admin maps "Titel", not `/Title` or `dc:title`.
- **`attribute`** is one of the type's declared attributes.
- **`overwrite`:**
  - `if_empty` (default): fills only what the uploader left empty;
  - `always`: the file wins over the uploader's input.

object-type-service validates on create and update (422 otherwise):
- folder types can't have mappings (folders have no file);
- the source must be in the vocabulary and the attribute declared;
- a `date` attribute takes only `created`/`modified`; other sources fill only `string` attributes;
- each attribute is a target at most once.

Validation runs against the type's new attribute list, so removing a mapped attribute is a 422 until
the mapping goes too. **`PUT` without `metadata_mappings` keeps the stored mappings.** That differs from
the other rule fields, which a `PUT` replaces. It is deliberate: older clients (an admin-ui that
predates this session, scripts) don't know the field and must not wipe it.

config-service exports and imports the field with the type, on create and update.

## Decision 2 — Extraction in document-service, pure and fail-safe

`document_service.embedded_metadata.extract(data, content_type)` reads:
- **PDF:** the document information dictionary via pypdf (already a dependency of rendering-service),
  with the XMP packet as a fallback for fields the dictionary leaves empty;
- **Office Open XML:** `docProps/core.xml` and `docProps/app.xml`;
- **OpenDocument:** `meta.xml`.

Dates become `YYYY-MM-DD`, the format the constraint engine accepts for `date` attributes.

**The upload is untrusted input:**
- XML goes through defusedxml, so entity declarations and external references are refused (a test
  covers it);
- a zip member over 1 MiB is skipped, and values are cut at 1,000 characters;
- the package layout decides the format, not the sniffed MIME type, because libmagic reports some
  Office files as `application/zip`;
- every parsing error means "no metadata". **An upload never fails because of its properties.**

## Decision 3 — The merge, and what happens when a value doesn't fit

In `POST /documents`, the file is read before the field preparation, and only when an object type is
given. `_prepare_document_fields` fetches the type once (it did so later anyway, for retention) and
merges per policy.

**A merged value can break the type's rules:** a pattern, a length, an allowed value. Two options:
- **(a) reject the upload** with the validation error. The uploader would see an error about a value
  they never entered.
- **(b) validate the merged attributes; if they fail, validate what the uploader sent.**

**Chosen: (b).** A file property is a suggestion. If the uploader's own attributes are invalid, the
error is the same as without mappings. The reference number is assigned after this step, so no number
is consumed by a failed attempt.

## Scope

- **New documents only:** `POST /documents`. Not new versions: a version's attributes belong to the
  document, and a silent change on check-in would surprise. Not the quarantine release path, which
  creates from a held file under a review decision.
- **The user-ui doesn't preview** the extracted values in the upload dialog. The values appear on the
  document after upload and can be edited there. A preview (an extraction endpoint called before the
  upload) is a candidate if users ask for it.
- No custom properties (`docProps/custom.xml`, PDF custom keys) and no EXIF/IPTC from images; the
  vocabulary can grow when a need is concrete.

## Consequences

- **object-type-service:** column `metadata_mappings` (ad-hoc migration), schemas, validation, tests.
- **document-service:** `embedded_metadata.py`, dependencies `pypdf` and `defusedxml`, the merge in
  `_prepare_document_fields`, unit and integration tests.
- **config-service:** `ObjectTypeExport.metadata_mappings`, export, import update field list, a
  round-trip test.
- **admin-ui:** the mapping editor in the object-type editor (`ObjectTypeMetadataMappings`), which
  offers only attributes that fit the chosen property and names mistakes before submitting.
