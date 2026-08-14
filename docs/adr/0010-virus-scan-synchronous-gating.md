# 0010 — Virus scan: synchronous gating in the upload path instead of asynchronous scan status

**Status:** accepted
**Context:** Concept 10.3, Session P5-S1

## Decision

The Document Service calls the new Virus Scan Service **synchronously,
before** persisting either the content or the metadata of an upload — both
during the initial creation (`POST /documents`) and during check-in of a new
version (`POST /documents/{id}/versions`). If the scan comes back negative,
the entire request is rejected with `422`: no document/version is created,
nothing is stored in the Storage Service. There is **no** new gating state
(e.g. `scan_status: pending/clean/infected`) on `document`/`document_version`.

If the Virus Scan Service is unreachable, the upload is likewise rejected
(`503`, fail-closed) rather than being silently let through.

## Rationale

- **10.3 explicitly requires** "virus scan mandatory before an upload is
  released". The Document Service's existing upload path
  (`POST /documents`/`POST /documents/{id}/versions`) stores content in the
  Storage Service immediately and makes it retrievable right away via
  `GET .../content` — a purely asynchronous consumption of
  `document.version.created` by a virus scan service would only react
  *after* this release and would violate that guarantee.
- **Existing precedent in the same service**: the Document Service already
  validates synchronously against the Object-Type Service
  (`object_type_client.validate(...)`, before the actual write) before
  accepting an upload. The virus scan follows the same, already-established
  pattern, instead of introducing a second, different kind of integration
  (asynchronous event gating).
- **No new gating state needed**: a `scan_status` column would have affected
  multiple places (every read access to content, rendering/OCR in P5-S2/S3
  would additionally have to check the state) and would have been a more
  invasive, more error-prone change than a single call before the write.
  Since the synchronous scan prevents an infected document from ever coming
  into existence in the first place, no downstream service ever needs to
  know about an intermediate state.
- **Fail-closed on unreachability**: the alternative (letting the upload
  through on scan failure) would contradict "mandatory" - an outage of the
  Virus Scan Service must not open a security hole.
- **Quarantine instead of deletion**: on a hit, the file is not discarded but
  stored via the Storage Service under `quarantine/{scan_id}`
  (traceability/evidentiary value, 10.3 itself makes no requirement here) -
  the Virus Scan Service, like the Document Service itself, holds no bytes
  of its own, but delegates to the Storage Service.
- **Engine swappable, but ClamAV not the default**: following the same
  plugin principle as the storage backends (3.3/3.8), the scan engine is a
  swappable interface (`ScanEngine`). The default is an
  `EicarSignatureEngine` (recognizes only the standardized EICAR test
  signature), not the obvious `ClamdEngine` against a `clamd` daemon:
  `clamd` downloads its signature database via `freshclam` on first start,
  which takes minutes in this development environment and requires reliable
  internet access to the ClamAV mirrors - not suitable for a reproducible
  `docker compose up`/test run. `ClamdEngine` is fully implemented (INSTREAM
  protocol) and can be enabled via `DMS_SCAN_ENGINE=clamd` once a `clamd` is
  operated separately.

## Consequences

- Upload latency now includes scan time (negligible with the
  `EicarSignatureEngine`, noticeably so with a real engine like `clamd`
  depending on file size) - accepted for this base scaffold, to be optimized
  later via chunked scanning/streaming if needed.
- The check-in path also scans when `expected_base_version_number` is
  already stale or a lock conflict exists (the scan runs before the actual
  conflict detection) - unnecessary but not incorrect work; no correctness
  gap.
- No dedicated release/deletion workflow for quarantined objects (restore,
  permanent deletion) - out of scope for this session.
- "Notifying the uploader" on a hit (10.3 does not mention this explicitly,
  but it is a natural expectation) is not yet implemented, since the
  Notification Service does not exist until P6-S2 - `virus_scan.completed`
  is already published and can be consumed there without any change to the
  Virus Scan Service.
- OCR (P5-S3) and Rendering/Preview (P5-S2) hook into
  `document.version.created`, which is only published *after* a clean scan
  - neither session needs to concern itself with the scan gating itself.
