# signature-service

**Responsibility:** Signature Service (concept 3.10) - eIDAS-compliant electronic signature (SES/AES/QES), a broker in front of external QTSPs via interchangeable signature-provider connectors (plugin principle like storage backends/CMIS, 3.3). This session (P6-S7) implements the basic framework + a genuinely working internal, self-signed connector for SES/AES, as well as the new "signature task" type in the Workflow Service (7.1) — QES via a real accredited QTSP is deliberately not part of this session (see [ADR 0025](../adr/0025-signature-service-internal-ca-and-connector-plugin.md)). **Since Post-Roadmap Phase 41 Session 1** ([ADR 0155](../adr/0155-internal-tsa-and-self-contained-pades-b-lta.md)): every signature is produced at the full PAdES-B-LTA (long-term archival) profile, via a new internal timestamp authority and a periodically extended archive-timestamp chain — see "PAdES-B-LTA" below.

**Concept reference:** 3.10, 2.1a, 7.1
**Own Postgres schema:** `signature` (tables `signature`, `internal_ca`, `internal_tsa`)

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/signatures` | Signs a document version (`document_id`, `level`: `ses`\|`aes`\|`qes`, `signer_principal_id`, optional `version_number`/`reason`) - `400` on non-PDF `content_type`, unknown principal, a level too low relative to the object type minimum, or a missing connector for the requested level; `404` on an unknown document/unknown version. On success, creates a **new document version** at document-service (see below). **Since P56-S1** ([ADR 0152](../adr/0152-maintenance-mode-service-to-service-enforcement-scoping.md) "Category A"): `503` while system-wide maintenance mode is active, checked before the document lookup — see "Emergency-Shutdown Interaction" below. **Since Phase 59 Session 3** ([ADR 0180](../adr/0180-signature-service-create-signature-permission-and-signer-identity-gate.md)): `401`/`403` gate, see "Authorization" below |
| `GET` | `/signatures?document_id=...` | Signatures of a document - `document_id` is **required since Phase 59 Session 3** (`400` if omitted, no more system-wide listing); `401`/`403` gate, see "Authorization" below |
| `GET` | `/signatures/{id}` | Single signature - `404`. **Since Phase 59 Session 3**: `401`/`403` gate, see "Authorization" below |
| `GET` | `/signatures/{id}/verify` | Re-verifies the signature against the bytes currently stored at document-service (`valid`, `integrity_intact`, `certificate_expired`, `errors[]`). **Since Phase 59 Session 3**: `401`/`403` gate, see "Authorization" below |
| `GET` | `/signature-config` | Current connector levels (since **Post-Roadmap Phase 22 Session 6**, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md)) — `id`/`type` structurally from `Settings.signature_providers`, `levels` live-editable, default row seeded from the previous env-var values on first call |
| `GET` | `/signatures/due-for-retimestamp` | **Since P71-S4**: system-wide list of signatures the PAdES-B-LTA poll loop will re-timestamp on its next tick — gated behind `admin.signature_config`, read-only (no manual trigger, see "PAdES-B-LTA" below) |
| `PUT` | `/signature-config` | Updates `levels` ONLY for the named connector `id`s (`[{id, levels}]`, ones not named keep their value) — takes effect **without a restart**; `422` on an unknown `id` ("can only edit existing entries"), empty `levels`, or `qes` for `type=internal` (the same rule as `SignatureProviderConfig._check_levels`) |
| `GET` | `/incoming-signatures?document_id=…[&version_number=…][&history=true]` | **Since P92-S1** ([ADR 0248](../adr/0248-validating-externally-signed-incoming-documents.md)): validation results for signatures in a document's versions - `summaries` (one per version) and `validations` (the latest run's rows; `history=true`: every run). `401`/`403` like the other reads (`document.read`) |
| `GET`/`PUT` | `/incoming-signatures/config` | **Since P92-S1**: `{enabled, revocation_mode: soft-fail\|hard-fail, outbound_fetching}`. `GET` ungated, `PUT` needs `admin.signature_config` |
| `GET`/`POST` | `/incoming-signatures/trust-anchors` | **Since P92-S1**: the installation anchors (`POST {label, certificate_pem}`: one CA certificate; `422` not a CA/not PEM, `409` already there). The listing starts with the internal CA (`source: internal`, `id: null`, not removable). Both need `admin.signature_config` |
| `DELETE` | `/incoming-signatures/trust-anchors/{id}` | **Since P92-S1**: removes an installation anchor (`404` unknown). Earlier results stay as they were |
| `POST` | `/incoming-signatures/revalidate` `{document_id, version_number?}` | **Since P92-S2**: queues the version (default: current) for another run, `202`. `document.read`; `404` unknown document/version. The run is added (`trigger: revalidation`, `requested_by`) |
| `POST`/`GET` | `/incoming-signatures/backfill` | **Since P92-S2**: `POST {folder_id?, created_from?, created_to?, only_unvalidated=true}` starts a background run over existing PDF versions (`202`; `422` without folder or range, `409` while one runs); `GET` shows its progress (`scanned`, `queued`, `skipped_validated`, `limit_reached`, `queue_pending`). Both `admin.signature_config` |
| `GET` | `/incoming-signatures/reports/{run_id}` | **Since P92-S2**: the run's ETSI TS 119 102-2 report (XML download). `document.read`; `404` unknown run or no report |
| `GET` | `/incoming-signatures/trusted-lists` | **Since P92-S2**: stored EU lists (territory, sequence number, issue date, next update, source, fetched at) and the latest fetch/import with its errors. `admin.signature_config` |
| `POST` | `/incoming-signatures/trusted-lists/refresh` | **Since P92-S2**: fetch now, in the background (`202`). `admin.signature_config` |
| `POST` | `/incoming-signatures/trusted-lists/import` | **Since P92-S2**: offline import of one list (multipart `file`): the LOTL first, then national lists (`422` wrong order, bad signature or not a list, `413` too large). `admin.signature_config` |
| `GET` | `/healthz` | Health check |

## Data Model

- `internal_ca`: singleton (`id=1`) - `certificate_pem`, `private_key_pem`, `created_at`. Self-signed internal root CA (RSA 2048, 20-year validity), generated on first start (`connectors/internal.generate_root_ca`), reused idempotently thereafter - a restart must not generate a new CA, otherwise previously issued signatures would no longer be verifiable.
- `internal_tsa`: singleton (`id=1`), **since Post-Roadmap Phase 41 Session 1** ([ADR 0155](../adr/0155-internal-tsa-and-self-contained-pades-b-lta.md)) - `certificate_pem`, `private_key_pem`, `created_at`. RFC 3161 timestamp authority certificate (TIME_STAMPING Extended Key Usage, critical), issued once from the internal root CA (`connectors/internal.issue_tsa_certificate`) and reused idempotently, same rationale as `internal_ca`.
- `signature`: `document_id`, `source_version_number` (the signed source version), `version_number` (the newly created, signed version), `level`, `connector_id`, `signer_principal_id`, `signer_display_name`, `certificate_subject`/`certificate_serial`/`certificate_not_before`/`certificate_not_after`, `reason`, `signed_at`, `last_timestamped_at` (nullable, **since Post-Roadmap Phase 41 Session 1** - `NULL` until the first periodic archive-timestamp-chain extension; the initial signature already embeds the first archive timestamp regardless, see "PAdES-B-LTA" below).

## Signature Provider Connectors (3.10, plugin principle like 3.3)

`SignatureProviderConnector` (ABC, `connectors/interface.py`): `sign(pdf_bytes, signer, level)`/`verify(pdf_bytes)`. The factory (`connectors/__init__.py`) dispatches on `type` with a stable `id` mapping (like `storage_service.backends.build_backend`, ADR 0017), configured via `DMS_SIGNATURE_PROVIDERS` (a JSON list). Default seed: `{id: "internal", type: "internal", levels: ["ses","aes"]}`. **Since Post-Roadmap Phase 22 Session 6** ([ADR 0091](../adr/0091-connector-operational-config-live-editable.md)): `levels` is additionally live-editable via `GET`/`PUT /signature-config` (a new DB singleton table `signature_config`, freshly read on every signing operation) — `id`/`type` remain structurally from `DMS_SIGNATURE_PROVIDERS`. Since then, `resolve_connector_for_level()` (`connectors/__init__.py`) takes an already-merged `list[SignatureProviderConfig]` instead of reading `Settings` directly.

- **`InternalSelfSignedConnector`** (`connectors/internal.py`, the only one actually implemented): issues a leaf certificate signed by the internal root CA per signing operation - `level="ses"` with a generic subject (`CN=DMS System (SES)`), `level="aes"` with a person-specific subject (`CN=<display name>`, `emailAddress=<email>`, from a real `auth-service` account check). Embeds the certificate into the PDF bytes via **pyHanko** (`SimpleSigner.load()` + `async_sign_pdf()`), at the full **PAdES-B-LTA** profile since Post-Roadmap Phase 41 Session 1 (see "PAdES-B-LTA" below; previously B-B only, and — due to a since-fixed bug — not even a real PAdES signature, see [ADR 0155](../adr/0155-internal-tsa-and-self-contained-pades-b-lta.md)). `verify()` uses `async_validate_pdf_signature()` with a `ValidationContext` whose only trust root is the internal CA (`allow_fetching=False`, `revocation_mode="soft-fail"` - a freshly signed, always-empty CRL is embedded/supplied for structural B-LT conformance, but no real OCSP or revocation-registry infrastructure exists, since nothing in this self-signed-CA design is ever meaningfully "revoked"). **`IncrementalPdfFileWriter`/`PdfFileReader` run with `strict=False`** (a bug fix after user feedback: `SigningError: ... hybrid cross-reference sections ...` on PDFs with a hybrid cross-reference table, as produced by, among others, LibreOffice) - pyHanko rejects such documents in its default strict mode (protection against "shadow attacks" when *validating* foreign PDFs); for signing/verifying a document uploaded within the own system, that is too common a legitimate case for a blanket rejection, a deliberate trade-off rather than an oversight.
- **`type: "qtsp"`** is provided for in the configuration schema but **not implemented** - a configuration attempt fails in the factory with a clear error message. No accredited external trust service provider available/testable in this session (see "Open Points").

**Authorization (Post-Roadmap Phase 38 Session 3)**: `PUT /signature-config` previously had no permission check at all — this service had no `permission_client` of any kind before this session. Now requires `X-DMS-Principal` + the new capability `admin.signature_config` (role `domain-admin-signature`) — a dedicated domain rather than reusing `admin.object_config`/`admin.storage`, since electronic-signature provider configuration is a materially different, more specialized concern than object-type schema or storage-backend administration. `GET /signature-config` remains ungated. See [ADR 0148](../adr/0148-admin-ui-authorization-full-alignment.md).

**Signature data-plane authorization (Phase 59 Session 3, [ADR 0180](../adr/0180-signature-service-create-signature-permission-and-signer-identity-gate.md))**: `POST /signatures` and the three `GET /signatures*` endpoints previously had no permission check at all — any authenticated caller could sign any document in the installation, or read any signature's data/verification result, regardless of their own access to the target document. Now gated via the caller's own `document.write` (create) / `document.read` (the three GET endpoints) against the target document's resource tree, checked directly against `permission-service` (`401` with no `X-DMS-Principal`, `403` without the permission) — existence-check-before-permission-check, matching `document-service`'s own established ordering convention. `POST /signatures` additionally requires `payload.signer_principal_id == X-DMS-Username` (`403` otherwise) — `X-DMS-Username` (Keycloak `preferred_username`), not `X-DMS-Principal` (Keycloak `sub`), since `signer_principal_id` is itself a username, compared against `auth-service`'s own `username` field by `resolve_signer`. This closes a spoofable-identity gap: previously any caller could set `signer_principal_id` to an arbitrary username, producing a signature falsely attributed to someone else. `GET /signatures` additionally requires a non-empty `document_id` (`400` otherwise) — no more unfiltered system-wide listing.

## Signing Creates a New Document Version (2.1a)

A PAdES signature necessarily changes the PDF bytes (that is the whole point of the cryptographic binding). `POST /signatures` loads the version to be signed from document-service via `document_client.py`, signs it, and checks in the signed bytes as a **new version** (`POST /documents/{id}/versions`, `expected_base_version_number = the signed source version`) - the unsigned original version remains accessible untouched. **Since Post-Roadmap Phase 38 Session 4** ([ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md)): `document_client.py`'s calls (`get_document`, `get_version_content`, `checkin_signed_version`) send a fixed `X-DMS-Principal: signature-service` header — these endpoints previously had no permission check at all. If the current main version is not the one being signed, document-service's existing optimistic conflict detection (4.2) automatically produces a conflict copy instead of moving the main version - no special handling needed here. The resulting `Signature` record references `source_version_number` (input) and `version_number` (the actual result, main or conflict version).

## PAdES-B-LTA (3.10, Post-Roadmap Phase 41 Session 1, ADR 0155)

Concept 3.10 explicitly requires the PAdES-B-LTA (long-term archival) profile for the disposal use case
(5.6). Every signature is now produced at this profile unconditionally (no per-signature profile
choice — 3.10 does not call for one):

- **Fixed a real, silent bug first**: `sign()` never set `subfilter=SigSeedSubFilter.PADES`. Without it,
  pyHanko defaults to `ADOBE_PKCS7_DETACHED` (plain PKCS#7) - every signature this service ever produced
  before this session was, structurally, not actually a PAdES signature at all, despite this service's
  own name and docs.
- **Internal TSA** (`internal_tsa` table): a dedicated TIME_STAMPING-purpose certificate, issued once
  from the internal root CA (`connectors/internal.issue_tsa_certificate`), feeds pyHanko's
  `DummyTimeStamper` - despite its "testing purposes" docstring, a genuine, complete, self-contained
  RFC 3161 signer that produces real `TSTInfo` CMS tokens. Same "internal instead of external"
  precedent as `internal_ca` itself (ADR 0025) and `federation-hub-service`'s hub identity (ADR 0085) -
  a real external TSA would require a business relationship this project cannot establish.
- **A freshly signed, always-empty CRL** (`connectors/internal._build_crl_der`, via `cryptography`'s
  `CertificateRevocationListBuilder`) satisfies B-LT's structural requirement for embedded revocation
  info, regenerated on every embed rather than cached.
- **`sign()` now sets** `embed_validation_info=True`, `validation_context=` (trust root + CRL),
  `use_pades_lta=True`, and passes the internal TSA as `timestamper=` to `signers.async_sign_pdf()`.
- **Periodic archive-timestamp-chain extension, entirely self-contained within this service** - a new
  background poll loop (`main._retimestamp_poll_loop`, `retimestamp_poll_interval_seconds` default 1
  day) queries `Signature` rows due for renewal (`last_timestamped_at` `NULL` and `signed_at`, or
  `last_timestamped_at` itself, older than `retimestamp_interval_days`, default 365) and calls
  `InternalSelfSignedConnector.extend_timestamp_chain` (pyHanko's `PdfTimeStamper.
  async_update_archival_timestamp_chain`) on each, checking the extended bytes back in via the
  already-existing `document_client` as a new document version (same "signed bytes get their own
  version" principle as the original signature) and stamping `last_timestamped_at`. **No new
  `archival-service` dependency** - this service's own `document_client` (fetch + check-in) is
  sufficient; see [`docs/services/archival-service.md`](archival-service.md) for the cross-reference.
  Per-item try/except, same fault-tolerant multi-phase poll-loop idiom used throughout this project.
  **No manual HTTP trigger** - same precedent as `document-service`'s retention poll loop.
- **Admin-UI visibility, since P71-S4**: new `GET /signatures/due-for-retimestamp` (gated behind
  `admin.signature_config`, the same capability already gating `PUT /signature-config` - a system-wide
  overview of the installation's signature estate, not a per-document read like `GET /signatures`,
  which deliberately requires `document_id` for RBAC scoping since Phase 59 Session 3) reuses
  `repository.list_signatures_due_for_retimestamp` with the real `Settings.retimestamp_interval_days` -
  the exact same query the poll loop itself runs, so the view can never drift from what the loop will
  actually pick up next. Surfaced in `admin-ui`'s `RetimestampStatus.tsx`, rendered below
  `SignatureConfig` on `/signature-config/`. **Deliberately still read-only**: the plan for this session
  proposed "an optional manual-trigger endpoint" alongside the visibility view - this session verified
  against ADR 0155 itself first and found it had already explicitly decided against exactly that,
  citing this same "no manual trigger" precedent above. No new justification was found to reopen that
  decision, so only the visibility half was built.

## Minimum Signature Level per Object Type (3.10)

`object-type-service` got an additive column `required_signature_level` (`ses`/`aes`/`qes`/`NULL`, only for `applies_to="document"`, see `docs/services/object-type-service.md`). `POST /signatures` queries it via `object_type_client.py` (only if the document has an object type) and rejects a requested level that is too low with `400`.

## Signer Existence Check (retrofit pattern from P6-S6)

`signer_principal_id` remains a self-reported body field (consistent with `triggered_by`/`approved_by`/`completed_by`/`lifted_by` throughout the project), but is checked against a real `auth-service` account (`auth_client.py`, `GET /users/service-directory`) and supplies display name/email for the AES certificate. `400` on an unknown principal. **Since Phase 50 Session 2**: previously authenticated as the technical account `users-admin` (`POST /login` on every call) — a real excess-privilege exposure, since `users-admin` is a full domain-admin account used here only for a read-only lookup. Now asserts the same fixed `X-DMS-Principal: signature-service` identity this service already uses against `document-service` (`document_client.py`), gated by a new, narrow, service-only capability (`service.user_lookup`, seeded role `service-user-lookup` in `permission-service`) — no login round-trip, no credential settings.

## Signature Task in the Workflow Service (7.1, since P6-S7)

`workflow-service`'s `spiff_adapter.py` switches the BPMN parser to `SpiffWorkflow.camunda.parser.CamundaParser` (still maps `manualTask` to `ManualTask`, but additionally populates `task_spec.extensions` from `bpmn:extensionElements/camunda:properties`). A `<bpmn:manualTask>` with `camunda:properties` `taskType=signature`/`requiredLevel=...` thereby becomes recognizable as a signature task, while technically remaining an ordinary manual task - no new BPMN element, no modeler tooling breakage, ~~no process-designer palette entry in this session (follows with P6-S8)~~ — **closed at P6-S8**: `apps/process-designer`'s `SignatureTaskPropertiesProvider`, see `docs/services/workflow-service.md`. The `document_id` to be signed travels via the existing generic `data` process variable.

`GET /instances/{id}/tasks` surfaces `extensions` per task. `POST .../tasks/{id}/complete` requires a `signature_id` field for a signature task; a new, thin `signature_client.py` (pattern like `permission_client.py` from P6-S6) checks with this service (`GET /signatures/{id}`) that the signature exists, matches the `document_id` stored in the task data, and has at least the required level - otherwise `400`. Details/example fixture: `docs/services/workflow-service.md`.

## Emergency-Shutdown Interaction (4.8)

`POST /signatures` is **not** on the gateway allow-list - during maintenance mode, the gateway automatically blocks this endpoint (like any other unlisted one) with `503` (default-deny, see [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md)) for any caller reached THROUGH the gateway. **Since P56-S1** ([ADR 0152](../adr/0152-maintenance-mode-service-to-service-enforcement-scoping.md), Category A): this alone left a residual gap the gateway's own check structurally cannot close - a direct, gateway-bypassing internal network call (any service on the same Docker network implicitly trusts its own network position, ADR 0005) would still have reached `POST /signatures` and cascaded into `document_client.checkin_signed_version` during an active lockdown. A local `_reject_during_maintenance` helper (same shape as `document-service`/`folder-service`'s P51-S4 originals) now also reads the gateway-forwarded `X-DMS-Maintenance-Active` header directly in `create_signature`, closing that gap without needing a second round trip to `permission-service`.

## Events

**Published** (stream `signature`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `signature.created` | `{version_number, level, signer_principal_id, connector_id}` |
| `signature.incoming.validated` | **Since P92-S1**: `{version_number, run_id, signature_count, worst_indication, best_qualification, changed_after_signing, trigger}`, since P92-S2 also `{requested_by, signers: [{subject, indication, qualification}]}` (subject: `document_id`; actor: the requesting principal, else `system:signature-validation`). In the audit ledger through audit-service's existing `signature.>` subscription (verified live in P92-S2) |
| `signature.trust_anchor.added` / `.removed` | **Since P92-S1**: `{label, fingerprint_sha256}` (subject: anchor id) |

**Consumed since P92-S1:** `document.>` with its own durable `signature-service-incoming` and `deliver_new` (only `document.created`/`document.version.created` are used). Until P92-S1 the service was a pure producer. Because of that consumer it is now in `scripts/run-tests.sh`'s `CONSUMER_SERVICES`.

## Validating Incoming Signatures (P92-S1, [ADR 0248](../adr/0248-validating-externally-signed-incoming-documents.md))

**Flow:** document-service publishes `document.created`/`document.version.created` with `content_type`,
`filename` and `version_number` → the consumer (`incoming.IncomingSignatureService.handle_event`) queues
versions a validator applies to → a runner fetches the version (refusing files above
`incoming_validation_max_bytes` without downloading), skips PDFs without `/ByteRange` (unsigned), runs the
validator in a thread pool, stores the run and publishes `signature.incoming.validated`. Own signatures take
the same path and are validated against the internal CA.

**Parts** (`validation/`):
- `interface.py`: the format-agnostic contract (`SignatureValidator.applies/validate`) and result model
  (`SignatureFinding`: indication per EN 319 102-1, sub-indication, qualification, trust source, signer,
  claimed and proven time, coverage, plain-German `failure_reason`, `technical_detail`).
- `pades.py`: every signature field of a PDF.
  - The strict reader; a refused file is one `INDETERMINATE` finding "Dateistruktur nicht prüfbar".
  - Signatures with a signature timestamp go through the LTA process (DSS and document timestamps
    count); B-B signatures through basic validation (§5.3), since the with-time process rejects them for
    the missing timestamp alone.
  - Document timestamps are listed (`kind: document_timestamp`) but not counted as signatures.
  - The modification analysis (pyHanko's default diff policy) decides coverage: `entire_document`,
    `permitted_changes` (form filling, annotations, further signatures, validation data), or
    `changed_after_signing`, which makes the finding `TOTAL-FAILED` / `MODIFIED_AFTER_SIGNING` whatever
    the cryptography says (the shadow-attack case).
  - Qualification (P92-S2): `qes` for a qualified certificate whose key is on a QSCD, `adesqc` for a
    qualified one without, `ades` for any other pass, `unknown` otherwise - only the EU trusted lists
    can establish "qualified" (pyHanko's assessment from the certificate's QcStatements and the TL's
    service entry); `qc_type` says `esign`/`eseal`/`web`.
  - The run's ETSI TS 119 102-2 report (P92-S2) covers every AdES-validated signature; it is stored in
    storage-service as `signature-validation-reports/{document_id}/{run_id}.xml` (`report_key` on the
    rows). pyHanko's public `generate_report` covers one signature, so its internal builders are joined.
    Document timestamps (validated through pyHanko's older API) aren't part of the report.
- `trust.py`: anchors only from the installation (internal CA, uploaded CA certificates). Certificates in
  the file can help build a path but never become anchors; the OS trust store is never used. Every anchor
  carries the path-length limit.
- `fetcher.py`: the fenced `aiohttp` session for pyHanko's OCSP/CRL/AIA fetchers.
  - Only `http`/`https` on ports 80/443.
  - Every resolved address must be public (no private, loopback, link-local, multicast, reserved, CGNAT
    or configured networks), checked in the resolver and, for IP literals, on request start.
  - Redirects only to the same host; a body cap per response; a timeout per request; an optional proxy
    (`DMS_SIGNATURE_VALIDATION_HTTP_PROXY`, whose own host is exempt).
  - A refusal looks like an unreachable server to pyHanko, so it can never turn into a pass.
- `worker.py`: a bounded queue (`incoming_validation_queue_size`) in front of `incoming_validation_workers`
  runners. Each validation runs in a thread with its own event loop and a time limit, so a flood of signed
  uploads delays results without blocking the signing API.

**EU trusted lists** (P92-S2, `validation/trusted_lists.py`):
- **Fetch:** the LOTL (`trusted_list_lotl_url`) and the national lists it names, optionally only
  `trusted_list_territories`. Through the fenced client, with cross-host redirects allowed (each hop still
  checked), gzip inflated with a cap on the inflated size (`trusted_list_max_bytes`). Runs in its own
  thread and event loop.
- **Checks before storage:** the LOTL against its signers (pyHanko's bundled last known set, or
  `trusted_list_lotl_signers_pem`; on failure the official bootstrap via the pivot LOTLs); each national
  list against the signers the LOTL names for its territory. Only passing lists are stored, in
  `trusted_list` (one row per kind and territory, replaced on refresh) - the table is the offline cache.
- **Schedule:** `_trusted_list_poll_loop`, a minute after start if nothing is stored, then every
  `trusted_list_poll_interval_seconds` (default daily, `0` = off; compose:
  `SIGNATURE_SERVICE_TRUSTED_LIST_POLL_INTERVAL_SECONDS`), only while `eu_trusted_lists` is on in the
  config. Each attempt is a row in `trusted_list_fetch` (lists stored, errors per territory, who).
- **Offline:** `POST /incoming-signatures/trusted-lists/import`, LOTL first; same checks.
- **Use:** the stored national lists become a pyHanko `TSPRegistry`, built in a thread once per change of
  the stored set (keyed by row ids and hashes) and shared read-only by runs. `trust.CombinedTrustManager`
  (a `TSPTrustManager`, which is what makes pyHanko run its qualification analysis) puts the
  installation's own anchors first and gives the EU anchors the same path-length limit. A pass through an
  EU anchor has `trust_source: eu_trusted_list`.
- **Measured live (2026-11-24):** LOTL sequence 398 plus 29 national lists stored in 2.4 minutes; EE
  refused the fetch (`403`), BE timed out. Building the registry and validating a PDF took about 5 s on
  first use; the container uses about 440 MB.

**Re-validation and backfill** (P92-S2): both queue jobs on the same worker (`trigger: revalidation`/
`backfill`, `requested_by`). The backfill pages through document-service's service-only
`GET /document-versions` (PDF versions of non-deleted documents, by folder - the folder's own documents -
and/or time range), skips versions that already have a result unless asked not to, and stops after
`incoming_backfill_max_versions`. Its status lives in memory (lost on restart; it can simply be repeated).
A job dropped because document-service was unreachable (P92-S1) is caught up this way.

**Revocation modes** (pyHanko's semantics): `soft-fail` (default) lets a signature pass when revocation
information can't be obtained; `hard-fail` makes it `INDETERMINATE` (`CERTIFICATE_CHAIN_GENERAL_FAILURE`).
A revoked certificate never passes in either mode. Embedded revocation data (DSS) is used first.

**Tables:** `incoming_signature_validation` (one row per signature and run, never updated),
`incoming_signature_summary` (the latest run per version: worst indication, best qualification,
`changed_after_signing`, `truncated`), `incoming_trust_anchor`, `incoming_validation_config` (singleton).

**Settings (operator bounds):** `incoming_validation_max_bytes` (50 MiB), `_max_signatures` (20),
`_max_path_length` (6), `_time_limit_seconds` (60), `_queue_size` (200), `_workers` (2),
`_fetch_timeout_seconds` (10), `_fetch_max_bytes` (2 MiB), `_blocked_networks` (extra CIDRs, e.g. public
pod ranges), `signature_validation_http_proxy`.

**Operations:** with outbound fetching on, signature-service needs egress to the CRL/OCSP/AIA hosts named
in the certificates it sees (or a proxy); with the EU trusted lists on, to `ec.europa.eu` and the national
list hosts (or the offline import). Switching fetching off leaves results `INDETERMINATE` where only
the network could decide (hard-fail) or unchecked revocation (soft-fail). A job whose version can't be
fetched (document-service unreachable) is dropped after a warning, since the event is already
acknowledged; the backfill in P92-S2 catches up.

**pyHanko quirks worked around** (0.36 / certvalidator 0.31):
- `ades_basic_validation` builds a plain CMS status whatever `status_cls` says, so the PDF integrity facts
  come from `EmbeddedPdfSignature.summarise_integrity_info()` (after `compute_integrity_info()`).
- `init_pkix_validation_state` copies `max_aa_path_length` only when `max_path_length` is set and then
  crashes through an intermediate; both qualifiers are set.

**Tests:** `test_incoming_validation.py` (generated test PKI in `tests/testpki.py`, a local aiohttp server
as CRL point and SSRF target): valid, intermediate and path-length limit, unknown CA, changed bytes,
shadow-style incremental update, a second signature as a permitted change, truncation, revoked via a
fetched CRL, unreachable revocation in both modes, SSRF (no request reaches the internal server), own
B-LTA signatures against the internal CA, a structure the strict reader refuses, the fence on its own
(addresses, URLs, a name resolving to loopback, redirect to another host, oversized body), anchor parsing.
`test_eu_trusted_lists.py` (P92-S2; generated XAdES-signed LOTL and TL in `tests/testtl.py`, so pyHanko's
real list checks run): description, LOTL/TL signature checks (wrong signer, forged TL, list changed after
signing), qualification (QES, AdES-QC, AdES from a listed CA, CA not listed), the ETSI report, fetch from a
local list server (store what passes, report what fails, territory filter). `test_incoming_trust_api.py`:
import order and checks, a qualified signature as `qes` with its report downloadable, re-validation as a
second run, backfill of versions missed while switched off, refresh from a list server.
`test_incoming_api.py`: end to end through document-service (upload → event → result), a later change as
version 2, unknown CA vs. unsigned PDF, own signature, switched off, config and anchor gating, anchor
lifecycle.

## Self-Registration (Concept 3.2a)

Registers itself with the registry on startup, identical pattern to every other service.

## Sensors (Concept 10.1)

None yet - follows in Phase 11.

## Tests

`uv run pytest services/signature-service/tests` - runs against a real Postgres instance and real calls to document-service/object-type-service/auth-service, no mocking of sibling services:

- Signing SES/AES with a real test PDF fixture generated via `pypdf`, a real pyHanko signature, a real new document version at document-service, real verification (`valid: true`).
- Object-type minimum-level gate (`400` on a level too low, `201` when sufficient).
- Rejection on a non-PDF document, unknown document, unknown signer principal, `level="qes"` without a configured connector.
- List/detail/verify incl. `404` cases.
- **35 tests since P71-S4** (+3: `test_due_for_retimestamp_without_principal_header_is_401`,
  `test_due_for_retimestamp_without_permission_is_403`,
  `test_due_for_retimestamp_excludes_a_freshly_signed_signature` — the last one deliberately does not
  also prove the "is due" case within `test_api.py`, since this `TestClient` instance's own real,
  ambient `_retimestamp_poll_loop` could race a deliberately-backdated signature and re-timestamp it
  before the test's own assertion runs, see the test's own docstring). Before that, 32 tests since
  Phase 59 Session 3 (+6: RBAC coverage for `POST /signatures` and the three `GET /signatures*` endpoints — see "Signature data-plane authorization" above; `test_create_signature_without_principal_header_is_401`, `test_create_signature_with_mismatched_signer_is_403`, `test_list_signatures_without_document_id_is_400`, `test_list_signatures_without_principal_header_is_401`, `test_get_signature_without_principal_header_is_401`, `test_verify_signature_without_principal_header_is_401`). Before that, 26 tests since P56-S1 (+1: `test_create_signature_rejected_during_maintenance_mode` — `X-DMS-Maintenance-Active: true` → `503`, fires before the `document_client.get_document` lookup, an unknown `document_id` still gets `503` not `404`). Before that, 25 tests since Post-Roadmap Phase 41 Session 1 (previously 18, +7, [ADR 0155](../adr/0155-internal-tsa-and-self-contained-pades-b-lta.md)): `test_connector_internal_pades_lta.py` (5) unit-tests `InternalSelfSignedConnector` directly (no cross-service dependency) - a real PAdES subfilter (regression test for the fixed silent bug), embedded validation info (`/DSS`), initial verification, and `extend_timestamp_chain` staying verifiable across repeated calls. `test_retimestamp_poll_loop.py` (2) exercises `main._run_retimestamp_tick` end-to-end (real document-service round trip) for both the "due" and "not yet due" cases, using its own engine/session bound to the test's own event loop rather than `TestClient`'s (which runs the app's lifespan-bound asyncpg engine on a separate event loop internally).
- Before that, 18 tests since Post-Roadmap Phase 38 Session 3 (previously 16, +2): 401/403 pair for `PUT /signature-config` — the service's first-ever RBAC coverage — before that, 16 tests since Post-Roadmap Phase 22 Session 6 (previously 11, +5, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md)): `GET /signature-config` returns the env-var defaults before the first `PUT`, `PUT` with an unknown connector `id`/empty `levels`/`qes` for `type=internal` each return `422`, an end-to-end test removes `aes` from `internal`'s levels and proves live (without a restart) that a subsequent AES signing attempt fails with `400`, while SES continues to work.
- A pure backend session, no browser test needed (for user-UI integration see `docs/services/user-ui.md`).

## Acting Identity (P85-S3, [ADR 0240](../adr/0240-acting-identity-from-verified-principal.md))

`POST /signatures` requires a principal (`401` otherwise) and refuses any on-behalf call (`403`): a
service may act for a user elsewhere, never to sign. The existing binding of `signer_principal_id` to the
verified `X-DMS-Username` (P59-S3) stays.

**P85-S4:** the signed version is checked in on behalf of the signer's principal; before, the
`created_by` field carried the signer's display name, which is not an identity.

## Open Points

- **Incoming (foreign) signatures are not validated** - `GET /signatures/{id}/verify` trusts only the internal CA and only covers signatures this service made. Scoped in P90-S6 ([ADR 0248](../adr/0248-validating-externally-signed-incoming-documents.md), built in Phase 92): asynchronous validation of every ingested version with EU trusted lists, installation anchors and a stored ETSI report.
- **QES completely unimplemented** - neither a real QTSP connector nor a test case for it exists; a signing attempt with `level="qes"` fails with `400` regardless of object type ("no connector configured"). Requires an external business relationship with an accredited trust service provider, see [ADR 0025](../adr/0025-signature-service-internal-ca-and-connector-plugin.md).
- ~~No PAdES-B-LTA/long-term archiving~~ — **implemented in Post-Roadmap Phase 41 Session 1** ([ADR 0155](../adr/0155-internal-tsa-and-self-contained-pades-b-lta.md)): internal TSA + periodic archive-timestamp-chain extension, see "PAdES-B-LTA" above.
- **No OCSP, only a CRL** - `GET /signatures/{id}/verify` checks integrity, trust chain, and certificate validity period; B-LT-required revocation info is now a real, freshly signed CRL (always empty - see "PAdES-B-LTA" above), not OCSP. For a self-signed internal CA with no revocation registry of any kind, an always-empty CRL is the honest ceiling here, not a placeholder for a "real" one still to come.
- ~~No process-designer palette entry for signature tasks - BPMN modeling remains a raw XML upload; a signature task must have its extension attributes set by hand in the XML.~~ — **closed at P6-S8**: `apps/process-designer`'s `SignatureTaskPropertiesProvider`, see `docs/services/workflow-service.md` "Signature Tasks with modeler support since P6-S8". Stale bullet, self-contradicted by `workflow-service.md`'s own text confirming this shipped — left un-struck here.
- **No PKCS#11/HSM support** - 3.10 explicitly mentions pyHanko also for hardware-token/HSM integration; this session uses exclusively in-memory-generated software keys.
- **Only PDF documents can be signed** - PAdES is PDF-specific (dictated by pyHanko itself); XAdES/CAdES for other formats are not implemented.
- ~~**The technical account `users-admin` also serves here as the internal service login**~~ — **closed in Phase 50 Session 2**: `signature-service` now asserts its own fixed identity (`X-DMS-Principal: signature-service`) against a new, narrow, service-only capability (`service.user_lookup`) instead of authenticating as the far broader `users-admin` domain-admin account — see "Signer Existence Check" above.
- ~~No Admin UI configuration for connectors - `DMS_SIGNATURE_PROVIDERS` is pure env-var configuration, consistent with storage backends (likewise without Admin UI configuration)~~ — **`levels` partially fixed in Post-Roadmap Phase 22 Session 6** ([ADR 0091](../adr/0091-connector-operational-config-live-editable.md)): `GET`/`PUT /signature-config` + new Admin UI page `/signature-config/`. Still env-var-only (deliberately, see ADR 0091 "Rationale"): the connector *list* itself (`id`/`type`), since new connectors need real infrastructure, not a pure configuration value.
- ~~No permission check on `POST /signatures`/`GET /signatures*`, spoofable `signer_principal_id`~~ — **closed in Phase 59 Session 3** ([ADR 0180](../adr/0180-signature-service-create-signature-permission-and-signer-identity-gate.md)): `document.write`/`.read` gate + `signer_principal_id == X-DMS-Username` identity check, see "Signature data-plane authorization" above. **No regression test exists for the real "caller lacks `document.write`/`.read`" `403` case** (unlike the sibling `document.write`-gate tests elsewhere) — `document.read`/`.write` are baseline "everyone" grants for any ordinary, non-teamspace document (ADR 0149), so constructing a genuine negative case needs a teamspace-scoped document (`inherit=False`) with its own fixture machinery, out of this session's scope.
