# 0248 — Validating externally signed incoming documents (scoping)

**Status:** accepted (scoping in P90-S6; built in Phase 92, scheduled 2026-11-23; stage 1 backend in P92-S1, trusted lists, reports, audit, re-validation and backfill in P92-S2)
**Context:** P90-S6 (Phase 90, user-selected from the comparison with an external reference system).

OG Doc signs documents itself (signature-service, PAdES-B-LTA with an internal CA, ADR 0025/0155). It
can verify only its own signatures: `GET /signatures/{id}/verify` trusts nothing but the internal CA.

Documents that arrive already signed are common:
- notices and contracts as signed PDFs;
- mail attachments with a detached `.p7s`;
- eGovernment messages as enveloping `.p7m`.

Today such a signature is stored as plain bytes. Nobody learns whether it is intact, who signed, whether
the certificate was qualified, or whether the PDF was changed after signing. The external reference
system validates incoming signatures, keeps a validation report with the document, and shows the result.

This ADR decides how to build that, and what stays out. Creating qualified signatures, HSM/PKCS#11 and
XAdES remain declined (plan, "Deliberately Not Included").

## What exists to build on

- **pyHanko 0.36**, already a signature-service dependency, covers most of the hard part:
  - `sign.validation.ades`: validation per ETSI EN 319 102-1 (basic, with time, past signature
    validation). Results carry an indication (`TOTAL-PASSED`/`TOTAL-FAILED`/`INDETERMINATE`) and a
    sub-indication.
  - `sign.validation.qualified`: fetches and parses the EU list of trusted lists (LOTL) and the member
    states' trusted lists, then assesses qualification (qualified certificate, QSCD), which separates
    QES from AdES.
  - `sign.validation.report`: validation reports per ETSI TS 119 102-2 (XML).
  - `generic_cms.async_validate_detached_cms`: detached CMS/CAdES.
  - The `etsi` extra adds `xsdata` and `signxml`, which trusted-list parsing and the reports need.
    `aiohttp` (extra `async-http`) is already in the lock file.
- **Every ingest path ends in document-service:** upload, mail-connector assignment, migration, WebDAV
  and Office add-ins use `POST /documents` or `POST /documents/{id}/versions`. Their events
  (`document.created`, `document.version.created`) are the one place to hook in.
- **user-ui** has a `SignaturesPanel` for the own signatures. **admin-ui** has `/signature-config/`.

## Decision 1 — Asynchronous, in signature-service, triggered by the document events

**Options:**
- **(a) Synchronously in document-service during upload.**
  - Validation may fetch revocation data and trusted lists over the network, which takes seconds and can
    time out.
  - P89-S4 showed what extra work per upload costs.
  - A bad signature must never reject an upload: a broken signature is a fact worth recording, not a
    reason to refuse the document.
- **(b) A new validation service.** It would duplicate signature-service's pyHanko setup, trust handling
  and connector configuration, for the same domain.
- **(c) signature-service consumes `document.created`/`document.version.created`**, the way
  ocr-service does.

**Chosen: (c).**
- The consumer fetches the version, decides whether it carries a signature, validates, stores the
  result and publishes `signature.incoming.validated`.
- The upload stays as fast as it is. The badge appears a moment later, like OCR text does.
- **Small additive change:** both events carry `content_type` and `filename`. Without them the consumer
  would download every version only to find it isn't a PDF.
- **Own signatures go through the same path:** a version created by signing is validated like any
  other, with the internal CA as a trust anchor labelled "intern". One display for every signature.
  Re-timestamped versions (ADR 0155) are validated again; that is cheap.
- **Durable consumer with `deliver_new`,** like the watch consumer (ADR 0256): the existing estate is
  not validated retroactively by replay. A backfill endpoint for admins (by folder or time range) is
  part of the build.

## Decision 2 — Formats: PAdES first, CAdES second, the model format-agnostic

**Stage 1, PAdES:** every signature field in a PDF is checked, including several signatures,
certification signatures with DocMDP, and document timestamps.

**Stage 2, CAdES:**
- **Detached (`.p7s`):** the hard part is pairing, not validation: which file does the `.p7s` sign?
  - In mail-connector the pair arrives in one message. The pairing rule is `name.ext` + `name.ext.p7s`,
    or a single attachment plus a single `.p7s`.
  - An upload in the user-ui would need a "Signaturdatei" field.
  - The `.p7s` is stored as its own document, linked to the signed one (`derivation_type` "signature").
- **Enveloping (`.p7m`):** the signed content is inside. The `.p7m` stays the original document; the
  extracted content becomes a derived document. That way it can be previewed, searched and OCRed.

**Format-agnostic model:**
- One validator interface (`applies(content_type, filename)`, `validate(...)`) per format.
- A result schema without PDF-specific fields at the top level.
- XAdES could slot in later: signxml can verify it, and it is already in the `etsi` extra. Creating
  XAdES or validating it is **not** part of this plan.

## Decision 3 — Trust: three anchor sources, chosen by the admin

1. **EU trusted lists (LOTL → national lists).**
   - Fetched by signature-service on a schedule (default daily) and cached in its database, with
     sequence number and next-update date. The LOTL's own signers are pinned by pyHanko's bootstrap.
   - Only this source can say "qualified".
   - **Offline installations:** the lists can be imported as files through admin-ui, or the source can
     be switched off.
2. **Installation anchors:** CA certificates an admin uploads, for example a partner authority's PKI or
   an internal company CA. Results based on these say "vertrauenswürdig (Installation)", never
   "qualifiziert".
3. **The internal CA**, for OG Doc's own signatures (as today).

**Revocation:** checked through OCSP/CRL from the certificates and the trusted-list services.
- Mode `soft-fail` by default: unreachable means `INDETERMINATE`, never `FAILED`.
- `hard-fail` is selectable.
- Embedded revocation data (PAdES-B-LT/LTA, a DSS dictionary) is used first, without a network call.

## Decision 4 — Outbound fetching is an SSRF surface and gets fenced

The URLs for AIA, CRL and OCSP come from the certificates in the uploaded file, so the uploader
chooses them. Fetching them blindly lets any user make the cluster call internal addresses
(`http://permission-service:8000/...`, cloud metadata endpoints).

**The build therefore sets these limits:**
- Fetching only through signature-service's own HTTP client (pyHanko's fetcher backend accepts an
  `aiohttp` session).
- Only `http`/`https`, standard ports, no redirects to other hosts.
- **Resolved addresses checked:** no private, loopback, link-local or cluster ranges.
- Timeouts and a response size cap.
- An optional egress proxy (`DMS_SIGNATURE_VALIDATION_HTTP_PROXY`).
- Fetching can be switched off entirely. The result is then `INDETERMINATE` where only the network
  could have decided.

Today Helm has no NetworkPolicy at all. That gap is broader than this feature. It is noted for P94
(operations) rather than solved here.

## Decision 5 — What a result is, and where it lives

**A table in signature-service, `incoming_signature_validation`,** one row per signature in a
document version:
- format, field or position, the indication and sub-indication;
- qualification: `qes` / `adesqc` / `ades` / `unknown`;
- signer subject and issuer, the claimed signing time, and the best proven time (from a timestamp);
- **coverage:** whole document, or changed afterwards (pyHanko's modification analysis: allowed form
  filling and annotations, or other changes);
- the trust source, the trusted-list sequence used, and `validated_at`.

**The ETSI report (XML)** is stored per validation run in storage-service and can be downloaded. It is
the evidence, and its validation time matters.

**A validation is a statement at a point in time.**
- Rows are never overwritten. A re-validation (admin, or a user with `document.read` asking for it)
  adds a run, so history stays visible.
- There is no automatic periodic re-validation. Preserving evidence for foreign signatures over
  decades (adding archive timestamps, TR-ESOR-style) is a separate topic and stays a candidate.

**A per-version summary for lists and badges:** the worst indication across signatures, the highest
proven qualification, and "nachträglich verändert" if any signature doesn't cover the version.

**Events and audit:**
- `signature.incoming.validated` goes to the audit ledger: who signed what, and the result.
- A notification for a failed signature is a later option, not part of the build.

## Decision 6 — Security of the validation itself

- **Foreign PDFs are validated strictly.** signature-service signs its *own* PDFs with
  `strict=False`, because LibreOffice's hybrid cross-reference tables are legitimate there.
  - For a foreign signature that leniency is exactly what shadow attacks exploit: content hidden or
    swapped by incremental updates after signing.
  - Validation uses pyHanko's strict reader and its modification analysis.
  - A file the strict reader refuses gets `INDETERMINATE` with the reason "Dateistruktur nicht
    prüfbar". It is not silently validated leniently.
- **Bounded work:**
  - a size cap per file, at most 20 signatures per version;
  - a time limit per validation, and a limit on certificate path length;
  - validation in a worker with a bounded queue, so a flood of signed uploads delays badges but never
    blocks signature-service's signing API.
- **No trust from the file:** certificates embedded in the PDF can help build a path. They never
  become anchors.

## Decision 7 — UI

- **user-ui:**
  - A badge in the document list (signed: valid / qualified / failed / not checkable).
  - The `SignaturesPanel` gets a "Eingegangene Signaturen" section: one row per signature with
    signer, time, qualification, coverage and the reason for a failure in plain German, the report
    download, and "erneut prüfen".
- **reviewer-ui:** the same badge on documents in a task.
- **admin-ui, `/signature-config/`:**
  - the trust sources: LOTL on/off, last fetch, sequence number, next update, offline import;
  - installation anchors (upload, list, remove), revocation mode and outbound fetching;
  - the backfill.
- Search filters ("gültig signiert") are a later candidate in query-service.

## Build (Phase 92, P92-S1 to S4)

| Session | Content |
|---|---|
| 1 | signature-service: validator interface, PAdES validator (strict, bounded), the table, the consumer with `deliver_new`, installation anchors and the internal CA, the fenced HTTP fetcher; document-service adds `content_type`/`filename` to both events; tests with a test PKI (valid, changed after signing, revoked, unknown CA, SSRF attempt). |
| 2 | EU trusted lists: scheduled fetch, cache, offline import, qualification; ETSI report in storage; `signature.incoming.validated` to audit; re-validation and backfill endpoints. |
| 3 | UI: user-ui badge and panel, reviewer-ui badge, admin-ui trust page; e2e with a signed PDF. |
| 4 (optional) | CAdES: detached pairing in mail-connector and in the upload form, enveloping `.p7m` with a derived content document. |

**Tests can't rely on real qualified signatures.** Sessions 1–3 use a generated test PKI and a local
trusted-list fixture (pyHanko's own tests work this way). A live fetch of the EU LOTL is a manual check
in session 2, and a real QES sample (e.g. from a public validation demo) one in session 3.

## Not in scope

- Creating qualified signatures (QTSP connector), PKCS#11/HSM, and creating XAdES/CAdES: still declined.
- Validating XAdES. The interface allows it, but there is no trigger.
- Long-term preservation of foreign signatures (archive timestamps, evidence records).
- Blocking or routing on the result (for example "only accept validly signed documents" in a workflow).
  That is a follow-up once results exist.
- A Helm NetworkPolicy for all services: noted for Phase 94.

## Consequences if built

- **signature-service** gains a consumer, a worker, two tables (validations; trust-list cache and
  anchors), outbound HTTP, and the `pyhanko[etsi,async-http]` extras.
- **document-service:** two event fields.
- **storage:** one report object per validation run.
- **Ops:** outbound HTTPS to `ec.europa.eu`, the national list hosts and certificate authorities, or
  the offline import. Both are documented in the operations docs.

## Implementation notes (P92-S1)

Built as decided, with these refinements:

- **Events:** `document.created` carries `version_number` too, not only `content_type`/`filename`, so the
  consumer needn't ask which version the event means. The consumer subscribes `document.>` with one
  durable (`signature-service-incoming`) and filters, the pattern every other consumer uses.
- **Revocation modes follow pyHanko's semantics.** Decision 3 reads as if `soft-fail` meant
  "unreachable → INDETERMINATE". In pyHanko, `soft-fail` lets a signature pass when revocation data can't
  be obtained, and `hard-fail` makes it INDETERMINATE (never FAILED). That pair is what was built:
  `soft-fail` is the forgiving default, `hard-fail` the strict choice. A certificate actually revoked
  never passes in either mode.
- **B-B signatures:** EN 319 102-1's with-time and LTA processes require a signature timestamp, and
  received PDFs often have none. Those go through basic validation (§5.3); signatures with a timestamp
  through the LTA process.
- **Changed after signing overrides the cryptography.** If the modification analysis finds changes beyond
  form filling, annotations, further signatures and validation data, the finding is `TOTAL-FAILED` with
  `MODIFIED_AFTER_SIGNING`, even though the signature still verifies over its own revision.
- **Unsigned PDFs are cheap:** a PDF without `/ByteRange` doesn't reach the worker, and a run without
  findings stores nothing.
- **A job is dropped when its version can't be fetched** (document-service unreachable), because the
  event was acknowledged on queueing. The admin backfill (P92-S2) catches up.
- **pyHanko 0.36 quirks** (basic validation ignores `status_cls`; certvalidator's path-length qualifier
  typo) are worked around and described in `docs/services/signature-service.md`.

## Implementation notes (P92-S2)

- **Trusted lists are checked before they are stored.** The LOTL against its signers (pyHanko's bundled
  set, an operator override, or the official bootstrap), each national list against the signers the
  LOTL names. The table of stored lists is the offline cache; the import takes the same path.
- **Qualification needs a `TSPTrustManager`.** pyHanko runs its qualification analysis only for that
  class, so a combined trust manager puts the installation's anchors first and the EU lists second.
  `qes` = qualified certificate on a QSCD, `adesqc` = qualified without, `ades` = any other pass.
- **One report per run**, joining pyHanko's per-signature builders; document timestamps aren't in it.
- **Audit came free:** audit-service already subscribes to `signature.>`. The event gained the signers
  and who asked.
- **Backfill by folder means the folder's own documents**, not its subtree; a time range covers bulk use.
  document-service got a service-only version listing for it.
- **Live (2026-11-24):** 29 of 31 national lists fetched and verified in 2.4 minutes (EE refuses
  automated fetches, BE timed out).

