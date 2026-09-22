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

No consumer - a pure producer, like `workflow-service`.

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

## Open Points

- **QES completely unimplemented** - neither a real QTSP connector nor a test case for it exists; a signing attempt with `level="qes"` fails with `400` regardless of object type ("no connector configured"). Requires an external business relationship with an accredited trust service provider, see [ADR 0025](../adr/0025-signature-service-internal-ca-and-connector-plugin.md).
- ~~No PAdES-B-LTA/long-term archiving~~ — **implemented in Post-Roadmap Phase 41 Session 1** ([ADR 0155](../adr/0155-internal-tsa-and-self-contained-pades-b-lta.md)): internal TSA + periodic archive-timestamp-chain extension, see "PAdES-B-LTA" above.
- **No OCSP, only a CRL** - `GET /signatures/{id}/verify` checks integrity, trust chain, and certificate validity period; B-LT-required revocation info is now a real, freshly signed CRL (always empty - see "PAdES-B-LTA" above), not OCSP. For a self-signed internal CA with no revocation registry of any kind, an always-empty CRL is the honest ceiling here, not a placeholder for a "real" one still to come.
- ~~No process-designer palette entry for signature tasks - BPMN modeling remains a raw XML upload; a signature task must have its extension attributes set by hand in the XML.~~ — **closed at P6-S8**: `apps/process-designer`'s `SignatureTaskPropertiesProvider`, see `docs/services/workflow-service.md` "Signature Tasks with modeler support since P6-S8". Stale bullet, self-contradicted by `workflow-service.md`'s own text confirming this shipped — left un-struck here.
- **No PKCS#11/HSM support** - 3.10 explicitly mentions pyHanko also for hardware-token/HSM integration; this session uses exclusively in-memory-generated software keys.
- **Only PDF documents can be signed** - PAdES is PDF-specific (dictated by pyHanko itself); XAdES/CAdES for other formats are not implemented.
- ~~**The technical account `users-admin` also serves here as the internal service login**~~ — **closed in Phase 50 Session 2**: `signature-service` now asserts its own fixed identity (`X-DMS-Principal: signature-service`) against a new, narrow, service-only capability (`service.user_lookup`) instead of authenticating as the far broader `users-admin` domain-admin account — see "Signer Existence Check" above.
- ~~No Admin UI configuration for connectors - `DMS_SIGNATURE_PROVIDERS` is pure env-var configuration, consistent with storage backends (likewise without Admin UI configuration)~~ — **`levels` partially fixed in Post-Roadmap Phase 22 Session 6** ([ADR 0091](../adr/0091-connector-operational-config-live-editable.md)): `GET`/`PUT /signature-config` + new Admin UI page `/signature-config/`. Still env-var-only (deliberately, see ADR 0091 "Rationale"): the connector *list* itself (`id`/`type`), since new connectors need real infrastructure, not a pure configuration value.
- ~~No permission check on `POST /signatures`/`GET /signatures*`, spoofable `signer_principal_id`~~ — **closed in Phase 59 Session 3** ([ADR 0180](../adr/0180-signature-service-create-signature-permission-and-signer-identity-gate.md)): `document.write`/`.read` gate + `signer_principal_id == X-DMS-Username` identity check, see "Signature data-plane authorization" above. **No regression test exists for the real "caller lacks `document.write`/`.read`" `403` case** (unlike the sibling `document.write`-gate tests elsewhere) — `document.read`/`.write` are baseline "everyone" grants for any ordinary, non-teamspace document (ADR 0149), so constructing a genuine negative case needs a teamspace-scoped document (`inherit=False`) with its own fixture machinery, out of this session's scope.
