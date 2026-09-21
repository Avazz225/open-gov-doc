# 0187 — shared `MaxBodySizeMiddleware` for unbounded uploads

**Status:** accepted
**Context:** P61-S2 (Phase 61, "Medium-Severity Findings" — second session of the sixth gap-analysis
round's live-code security sweep). The identical gap — an unbounded upload read before expensive
processing, with no size cap anywhere in-process or at any layer in front of these services (confirmed:
no `client_max_body_size`/equivalent at the gateway or any nginx config in this project) — was found
independently in three unrelated services: `virus-scan-service`'s `POST /scan` (`await file.read()`),
`rendering-service`'s several render/convert/export endpoints (`await file.read()` before PDF parsing/
rasterization/a real `soffice --headless` subprocess with only a 90s timeout), and `storage-service`'s
`upload_object`/`upload_archive_copy` (`await request.body()`). Unlike every other finding in this
gap-analysis round, which was genuinely service-specific, this one repeats verbatim across three services
— worth a shared fix, per the plan's own suggestion.

## Decision

**A shared `dms_common.MaxBodySizeMiddleware`, not a per-endpoint check.** Investigated the mechanics
first: for a `File(...)`/`UploadFile` parameter, FastAPI/Starlette already fully reads the upload into
memory (or a spooled temp file) to populate `UploadFile` **before the endpoint function even runs** — a
per-endpoint check placed after that point (the plan's own literally-worded suggestion, "reject on...
actual byte count above a threshold before any processing begins") is already too late to prevent the
read itself; it would only stop the OVERSIZED payload from reaching the actually-expensive downstream step
(virus scanning, PDF rasterization). A middleware instead inspects the `Content-Length` request header and
rejects with `413` **before routing/multipart-parsing ever starts**, for every route in the service
uniformly — genuinely preventing the memory cost, not just deferring it past the framework's own buffering.

- New `libs/dms-common/src/dms_common/upload_limits.py`: `MaxBodySizeMiddleware(BaseHTTPMiddleware)`,
  checking `request.headers.get("content-length")` against a configured `max_bytes`.
- New `BaseServiceSettings.max_upload_size_bytes` (shared default: 200 MiB) — a service with a materially
  different real need can override it with its own default, same as every other `BaseServiceSettings`
  field; none of the three affected services needed to.
- Wired into all three services via one `app.add_middleware(MaxBodySizeMiddleware,
  max_bytes=settings.max_upload_size_bytes)` call each, placed at module level immediately after
  `app = FastAPI(...)` (FastAPI forbids adding middleware once the app has started — the same constraint
  each service's own sensor-bootstrap comment already documents).

## Rationale

- **Accepted residual gap, stated honestly**: a request with no `Content-Length` header at all (genuinely
  chunked-encoded, which real upload clients rarely if ever use for file uploads) is not caught by this
  pre-emptive check — a narrower, explicitly documented limitation (in the module's own docstring and this
  ADR), not a claim of full streaming enforcement. Building true streaming enforcement (capping bytes as
  they arrive, regardless of whether `Content-Length` was declared) would need a lower-level ASGI body
  wrapper, a materially larger change for a narrow edge case not indicated as a real, exploited pattern by
  this round's research.
- **Why one shared middleware, not three copies**: the exact same gap, the exact same fix shape, in three
  unrelated services — this is precisely the situation the plan itself flagged as worth a shared helper,
  unlike this round's other findings.
- **Why 200 MiB as the shared default**: covers real-world scanned-document/PDF sizes (this project's
  actual document domain) with reasonable headroom, without being so large it defeats the point of having
  a limit at all. Not tuned per-service since none of the three affected services showed a materially
  different real need during investigation.

## Consequences

- New behavior: `POST /scan` (virus-scan-service), every render/convert/export endpoint
  (rendering-service), and the object-upload endpoints (storage-service) now `413` for a request whose
  declared `Content-Length` exceeds `max_upload_size_bytes` (200 MiB by default), before any body read or
  expensive processing begins.
- `libs/dms-common/pyproject.toml`: new `starlette>=0.38` dependency (the middleware only needs Starlette,
  not the full FastAPI package).
- New tests: `dms-common` +5 (`test_max_upload_size_bytes_default_and_env_override` in `test_settings.py`;
  `test_request_under_limit_passes_through`, `test_request_over_content_length_limit_rejected_with_413`,
  `test_request_exactly_at_limit_passes_through`, `test_request_without_content_length_header_is_not_
  blocked` in the new `test_upload_limits.py`, against a minimal standalone Starlette app — no need to
  spin up any of the three real services to exercise the shared mechanism itself). 11/11 total for
  `dms-common`.
- **Found and fixed a THIRD occurrence of the same pre-existing regression** already found twice this
  round (P60-S1's ocr-service fix): `rendering-service`'s and `document-service`'s own
  `_delete_storage_object_for_version`/inline equivalent test helpers called `storage-service`'s
  `DELETE /objects/{key}` directly with no identity header, broken since P59-S2's trusted-caller gate
  shipped — fixed the same way (a fixed `X-DMS-Principal` matching one of the six trusted callers).
  `document-service` 398/398, `rendering-service` 103/103 (unchanged counts — pure test-fixture fixes, no
  new tests needed for this incidental fix), `virus-scan-service` 38/38, `storage-service` 162/162, all
  unaffected in count by the middleware itself (no existing test's payload approaches 200 MiB). `ruff`
  clean across all touched packages (same pre-existing, unrelated repo-wide failures confirmed out of
  scope again).
- All three services (plus `dms-common`, picked up automatically on rebuild) rebuilt/redeployed.
  **Live-verified against the real running stack**: `curl` with a `Content-Length: 999999999999` header
  against each of the three services confirmed `413`, fired before any body was even read (a 1-byte actual
  body sent, rejected purely on the declared header); each service's own `/healthz` (small/no body)
  confirmed still working normally, proving the middleware doesn't interfere with ordinary requests.
