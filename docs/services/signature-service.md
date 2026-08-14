# signature-service

**Responsibility:** Signature Service (concept 3.10) - eIDAS-compliant electronic signature (SES/AES/QES), a broker in front of external QTSPs via interchangeable signature-provider connectors (plugin principle like storage backends/CMIS, 3.3). This session (P6-S7) implements the basic framework + a genuinely working internal, self-signed connector for SES/AES, as well as the new "signature task" type in the Workflow Service (7.1) — QES via a real accredited QTSP is deliberately not part of this session (see [ADR 0025](../adr/0025-signature-service-internal-ca-and-connector-plugin.md)).

**Concept reference:** 3.10, 2.1a, 7.1
**Own Postgres schema:** `signature` (tables `signature`, `internal_ca`)

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/signatures` | Signs a document version (`document_id`, `level`: `ses`\|`aes`\|`qes`, `signer_principal_id`, optional `version_number`/`reason`) - `400` on non-PDF `content_type`, unknown principal, a level too low relative to the object type minimum, or a missing connector for the requested level; `404` on an unknown document/unknown version. On success, creates a **new document version** at document-service (see below) |
| `GET` | `/signatures?document_id=...` | Signatures of a document |
| `GET` | `/signatures/{id}` | Single signature - `404` |
| `GET` | `/signatures/{id}/verify` | Re-verifies the signature against the bytes currently stored at document-service (`valid`, `integrity_intact`, `certificate_expired`, `errors[]`) |
| `GET` | `/signature-config` | Current connector levels (since **Post-Roadmap Phase 22 Session 6**, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md)) — `id`/`type` structurally from `Settings.signature_providers`, `levels` live-editable, default row seeded from the previous env-var values on first call |
| `PUT` | `/signature-config` | Updates `levels` ONLY for the named connector `id`s (`[{id, levels}]`, ones not named keep their value) — takes effect **without a restart**; `422` on an unknown `id` ("can only edit existing entries"), empty `levels`, or `qes` for `type=internal` (the same rule as `SignatureProviderConfig._check_levels`) |
| `GET` | `/healthz` | Health check |

## Data Model

- `internal_ca`: singleton (`id=1`) - `certificate_pem`, `private_key_pem`, `created_at`. Self-signed internal root CA (RSA 2048, 20-year validity), generated on first start (`connectors/internal.generate_root_ca`), reused idempotently thereafter - a restart must not generate a new CA, otherwise previously issued signatures would no longer be verifiable.
- `signature`: `document_id`, `source_version_number` (the signed source version), `version_number` (the newly created, signed version), `level`, `connector_id`, `signer_principal_id`, `signer_display_name`, `certificate_subject`/`certificate_serial`/`certificate_not_before`/`certificate_not_after`, `reason`, `signed_at`.

## Signature Provider Connectors (3.10, plugin principle like 3.3)

`SignatureProviderConnector` (ABC, `connectors/interface.py`): `sign(pdf_bytes, signer, level)`/`verify(pdf_bytes)`. The factory (`connectors/__init__.py`) dispatches on `type` with a stable `id` mapping (like `storage_service.backends.build_backend`, ADR 0017), configured via `DMS_SIGNATURE_PROVIDERS` (a JSON list). Default seed: `{id: "internal", type: "internal", levels: ["ses","aes"]}`. **Since Post-Roadmap Phase 22 Session 6** ([ADR 0091](../adr/0091-connector-operational-config-live-editable.md)): `levels` is additionally live-editable via `GET`/`PUT /signature-config` (a new DB singleton table `signature_config`, freshly read on every signing operation) — `id`/`type` remain structurally from `DMS_SIGNATURE_PROVIDERS`. Since then, `resolve_connector_for_level()` (`connectors/__init__.py`) takes an already-merged `list[SignatureProviderConfig]` instead of reading `Settings` directly.

- **`InternalSelfSignedConnector`** (`connectors/internal.py`, the only one actually implemented): issues a leaf certificate signed by the internal root CA per signing operation - `level="ses"` with a generic subject (`CN=DMS System (SES)`), `level="aes"` with a person-specific subject (`CN=<display name>`, `emailAddress=<email>`, from a real `auth-service` account check). Embeds the certificate into the PDF bytes via **pyHanko** (`SimpleSigner.load()` + `async_sign_pdf()`, PAdES-B-B). `verify()` uses `async_validate_pdf_signature()` with a `ValidationContext` whose only trust root is the internal CA (`allow_fetching=False`, `revocation_mode="soft-fail"` - no real OCSP/CRL infrastructure available). **`IncrementalPdfFileWriter`/`PdfFileReader` run with `strict=False`** (a bug fix after user feedback: `SigningError: ... hybrid cross-reference sections ...` on PDFs with a hybrid cross-reference table, as produced by, among others, LibreOffice) - pyHanko rejects such documents in its default strict mode (protection against "shadow attacks" when *validating* foreign PDFs); for signing/verifying a document uploaded within the own system, that is too common a legitimate case for a blanket rejection, a deliberate trade-off rather than an oversight.
- **`type: "qtsp"`** is provided for in the configuration schema but **not implemented** - a configuration attempt fails in the factory with a clear error message. No accredited external trust service provider available/testable in this session (see "Open Points").

## Signing Creates a New Document Version (2.1a)

A PAdES signature necessarily changes the PDF bytes (that is the whole point of the cryptographic binding). `POST /signatures` loads the version to be signed from document-service via `document_client.py`, signs it, and checks in the signed bytes as a **new version** (`POST /documents/{id}/versions`, `expected_base_version_number = the signed source version`) - the unsigned original version remains accessible untouched. If the current main version is not the one being signed, document-service's existing optimistic conflict detection (4.2) automatically produces a conflict copy instead of moving the main version - no special handling needed here. The resulting `Signature` record references `source_version_number` (input) and `version_number` (the actual result, main or conflict version).

## Minimum Signature Level per Object Type (3.10)

`object-type-service` got an additive column `required_signature_level` (`ses`/`aes`/`qes`/`NULL`, only for `applies_to="document"`, see `docs/services/object-type-service.md`). `POST /signatures` queries it via `object_type_client.py` (only if the document has an object type) and rejects a requested level that is too low with `400`.

## Signer Existence Check (retrofit pattern from P6-S6)

`signer_principal_id` remains a self-reported body field (consistent with `triggered_by`/`approved_by`/`completed_by`/`lifted_by` throughout the project), but is checked against a real `auth-service` account (`auth_client.py`, `GET /users`, authenticating as the technical `users-admin` account - `GET /users` has been gated since P6-S5) and supplies display name/email for the AES certificate. `400` on an unknown principal.

## Signature Task in the Workflow Service (7.1, since P6-S7)

`workflow-service`'s `spiff_adapter.py` switches the BPMN parser to `SpiffWorkflow.camunda.parser.CamundaParser` (still maps `manualTask` to `ManualTask`, but additionally populates `task_spec.extensions` from `bpmn:extensionElements/camunda:properties`). A `<bpmn:manualTask>` with `camunda:properties` `taskType=signature`/`requiredLevel=...` thereby becomes recognizable as a signature task, while technically remaining an ordinary manual task - no new BPMN element, no modeler tooling breakage, no process-designer palette entry in this session (follows with P6-S8). The `document_id` to be signed travels via the existing generic `data` process variable.

`GET /instances/{id}/tasks` surfaces `extensions` per task. `POST .../tasks/{id}/complete` requires a `signature_id` field for a signature task; a new, thin `signature_client.py` (pattern like `permission_client.py` from P6-S6) checks with this service (`GET /signatures/{id}`) that the signature exists, matches the `document_id` stored in the task data, and has at least the required level - otherwise `400`. Details/example fixture: `docs/services/workflow-service.md`.

## Emergency-Shutdown Interaction (4.8)

`POST /signatures` is **not** on the gateway allow-list - during maintenance mode, the gateway automatically blocks this endpoint (like any other unlisted one) with `503` (default-deny, see [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md)). No special handling needed, since signing is a write, non-time-critical admin/business action.

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
- **16 tests since Post-Roadmap Phase 22 Session 6** (previously 11, +5, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md)): `GET /signature-config` returns the env-var defaults before the first `PUT`, `PUT` with an unknown connector `id`/empty `levels`/`qes` for `type=internal` each return `422`, an end-to-end test removes `aes` from `internal`'s levels and proves live (without a restart) that a subsequent AES signing attempt fails with `400`, while SES continues to work.
- A pure backend session, no browser test needed (for user-UI integration see `docs/services/user-ui.md`).

## Open Points

- **QES completely unimplemented** - neither a real QTSP connector nor a test case for it exists; a signing attempt with `level="qes"` fails with `400` regardless of object type ("no connector configured"). Requires an external business relationship with an accredited trust service provider, see [ADR 0025](../adr/0025-signature-service-internal-ca-and-connector-plugin.md).
- **No PAdES-B-LTA/long-term archiving** - only PAdES-B-B implemented (no timestamp-authority countersigning). 3.10 explicitly names B-LTA for records disposal (5.6) - a future retrofit would need a real timestamp authority.
- **No OCSP/CRL revocation check** - `GET /signatures/{id}/verify` only checks integrity and the certificate's validity period. For a self-signed internal CA without real revocation-list infrastructure, this is the only honest verification depth.
- **No process-designer palette entry for signature tasks** - BPMN modeling remains a raw XML upload; a signature task must have its extension attributes set by hand in the XML. Follows with P6-S8.
- **No PKCS#11/HSM support** - 3.10 explicitly mentions pyHanko also for hardware-token/HSM integration; this session uses exclusively in-memory-generated software keys.
- **Only PDF documents can be signed** - PAdES is PDF-specific (dictated by pyHanko itself); XAdES/CAdES for other formats are not implemented.
- **The technical account `users-admin` also serves here as the internal service login** - as with notification-service (P6-S6), signature-service authenticates for the signer existence check as a foreign technical account instead of its own identity (see ADR 0024 "Consequences" for the already-noted recommendation to revisit this).
- ~~No Admin UI configuration for connectors - `DMS_SIGNATURE_PROVIDERS` is pure env-var configuration, consistent with storage backends (likewise without Admin UI configuration)~~ — **`levels` partially fixed in Post-Roadmap Phase 22 Session 6** ([ADR 0091](../adr/0091-connector-operational-config-live-editable.md)): `GET`/`PUT /signature-config` + new Admin UI page `/signature-config/`. Still env-var-only (deliberately, see ADR 0091 "Rationale"): the connector *list* itself (`id`/`type`), since new connectors need real infrastructure, not a pure configuration value.
