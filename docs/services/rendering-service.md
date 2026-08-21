# rendering-service

**Responsibility:** Rendering/preview + renditions (Concept 3.7/2.4) — automatically generates permanently persisted previews/renditions for new document versions and provides on-demand rendering functions (watermarking, PDF export, document redaction).
**Concept Reference:** 3.7, 2.4
**Own Postgres schema:** `rendering` (`rendition`).

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/renditions?document_id=...&version_number=...&status=...` | Generated renditions/previews for a version (`version_number` optional — without it: all versions of the document) — since **P19-S8** `rendering.read`-gated. Since **Post-Roadmap Phase 20 Session 7**, `document_id` is also optional (previously required) and a new `status` filter was added — without `document_id` this returns a cross-document list, the basis for the new Admin UI view of `failed_permanent` renditions ([ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)) |
| `GET` | `/renditions/{id}` | Single rendition (metadata) — 404 on unknown `id`; since **P19-S8** `rendering.read`-gated |
| `GET` | `/renditions/{id}/content` | Bytes of the rendition (proxy to the Storage Service) — 404 on unknown `id`, 409 on status `failed`/`failed_permanent`; since **P19-S8** `rendering.read`-gated |
| `POST` | `/renditions/{id}/retry` | Manual restart of a `failed_permanent` rendition (since **P20-S4**, [ADR 0080](../adr/0080-rendering-ocr-service-retry-backoff-failed-permanent.md)) — `404` on unknown `id`, `409` if `status != "failed_permanent"`, otherwise an immediate retry for ONLY the affected renderer; `rendering.write`-gated |
| `POST` | `/render/watermark` | Multipart (`file`: PDF, `text`) → on-demand watermarking, returns the stamped PDF directly, **without** persisting it; since **P19-S8** ([ADR 0073](../adr/0073-ocr-rendering-virus-scan-rbac.md)) `rendering.write`-gated |
| `POST` | `/render/convert-to-pdf` | Multipart (`file`: any `PdfArchiveRenderer`-supported format) → on-demand PDF conversion, same dispatch as the automatic pipeline but without persisting a `Rendition` row; since **Post-Roadmap Phase 28** ([ADR 0107](../adr/0107-pdf-export-two-pass-merge-subnumbering.md)) `rendering.write`-gated |
| `POST` | `/render/export/document` | Multipart (`file`, `title`, `history_position`, `history`: JSON array) → Pass A of the PDF export feature — converts + merges with the (already document-service-resolved) export history, stamps a local page-number footer; since **Post-Roadmap Phase 28** ([ADR 0107](../adr/0107-pdf-export-two-pass-merge-subnumbering.md)) `rendering.write`-gated |
| `POST` | `/render/export/folder` | Multipart (`titles`: repeated form field, `files`: repeated, each already the output of `/render/export/document`) → Pass B — table of contents, bookmarks, global page-number footer; since **Post-Roadmap Phase 28** ([ADR 0107](../adr/0107-pdf-export-two-pass-merge-subnumbering.md)) `rendering.write`-gated |
| `POST` | `/render/pdf-page-count` | Multipart (`file`: PDF) → `{page_count}`, since **Post-Roadmap Phase 31 Session 4** ([ADR 0115](../adr/0115-document-redaction-genuine-content-removal.md)) — for the redaction UI's page navigation; `rendering.write`-gated |
| `POST` | `/render/pdf-page-image` | Multipart (`file`: PDF, `page_number`) → PNG raster of that page (any PDF, not just scans — see "Document Redaction" below); `rendering.write`-gated |
| `POST` | `/render/redact` | Multipart (`file`: PDF, `regions`: JSON array of `{page_number, x, y, width, height}` fractions) → the PDF with those regions' content genuinely removed (not just covered), returned directly, **without** persisting it; `rendering.write`-gated |
| `GET` | `/healthz` | Health check |

The `id` of a rendition is a natural key `{document_id}:{version_number}:{rendition_type}` (see Data Model) — not a random UUID.

## Automatic Rendition Pipeline (2.4) Instead of a Cache

The Rendering Service consumes `document.created` (first version, `version_number` implicitly `1`) and `document.version.created` (check-in, `version_number` in the payload) from the Document Service — both dock in **after** the scan gating from P5-S1, since Document Service only publishes these events after a successful virus scan and successful write (ADR 0010). For each new version:

1. Metadata (`filename`, `content_type`) and original content are obtained via the **Document Service's HTTP API** (`GET .../versions/{n}` and `.../versions/{n}/content` respectively) — the Rendering Service knows neither its data model nor the content-addressed storage key directly (3.1).
2. All rules whose source format matches (`renderers/__init__.py`, see table below) are applied independently — if one fails (e.g. a corrupted `.pdf`), the others are still generated; the failed rule is recorded with `status="failed"` and `error_message` instead of aborting the whole process.
3. Every result is stored permanently via the **Storage Service** (`renditions/{document_id}/{version_number}/{rendition_type}`) — **no** cache/Redis/in-memory storage, as explicitly required by 2.4: resilience must not depend on the functioning of the very renderer it is meant to safeguard.
4. Reprocessing the same version (e.g. NATS redelivery) overwrites the existing row (natural primary key) instead of accumulating duplicates.

A single broad subject subscription (`document.>`, not two individual subscriptions) with in-handler dispatch by `event_type` — the same pattern as `permission-service/structure_consumer.py`, since a JetStream durable consumer name would be reserved per subscribed subject. Other `document.>` events (metadata update, deletion, force unlock) do not trigger rendering.

### Retry & Backoff (Post-Roadmap Phase 20 Session 4, [ADR 0080](../adr/0080-rendering-ocr-service-retry-backoff-failed-permanent.md))

A `status="failed"` (renderer plugin error) is no longer **immediately terminal** since then: `attempts`
is incremented, `next_retry_at` is set via `compute_backoff_seconds` (`libs/dms-retry`) — a new,
standalone `_rendition_retry_poll_loop` (interval `rendering_retry_poll_interval_seconds`, default
60s) picks up due renditions. **Difference from `ocr-service`**: since a version CAN have MULTIPLE
rendition rows (one per applicable rule), the retry specifically calls back ONLY the one
affected renderer (`renderers.get_renderer_by_type`/`pipeline.retry_rendition`), not the
entire `process_version` rule cascade — otherwise already-successful renditions would be needlessly
regenerated. Only after `max_rendering_attempts` (default 5) unsuccessful attempts does `status` switch to the
true terminal status `failed_permanent`, at which point `POST /renditions/{id}/retry` allows an immediate manual
restart (first resets `attempts`/`error_message`/`next_retry_at`, then performs a genuine new
attempt).

**Retroactive processing on first start**: since no `deliver_new` is set (unlike `permission-service`/`audit-service`), a fresh durable consumer catches up on the entire past event history on its very first start — documents uploaded before this session are thus retroactively fitted with renditions once the service runs for the first time. This is intended behavior (backfill), not a race condition.

## Rendition Rules (Plugin Principle Like Storage Backends, 3.3/3.8)

| Source Format | Target | Renderer | `rendition_type` |
|---|---|---|---|
| Raster images (`image/*`) | PNG thumbnail, max. 256×256 | `ThumbnailRenderer` (Pillow) | `thumbnail` |
| `.docx` | `.txt` text extraction | `DocxTextExtractionRenderer` (python-docx) | `substitute_text` |
| `.pptx` | `.txt` text extraction per slide | `PptxTextExtractionRenderer` (python-pptx) | `substitute_text` |
| `.ods` | `.txt` text extraction per sheet | `OdsTextExtractionRenderer` (odfpy, added later as a bugfix after user feedback — `.ods` previously had no renderer at all) | `substitute_text` |
| `.pdf`/raster images/office formats (`.docx`/`.pptx`/`.xlsx`/`.odt`/`.ods`/`.odp`/`.rtf`/`.txt`) | Universal PDF/A archive copy | `PdfArchiveRenderer` (see below) | `pdf_archive` |

New rules are added by registering another `Renderer` class in `renderers/__init__.py`, without changing existing code (`RENDERERS` list, `select_renderers()`).

**Deliberate deviations from the concept's example text, the same trade-off as ClamdEngine vs. EicarSignatureEngine in P5-S1/ADR 0010** — real functionality reachable without an external system dependency instead of a placeholder:

- **Image-based/scanned documents were deliberately not served in P5-S2** (they would have needed an OCR text layer as a basis, 3.9) — the follow-up effect from P5-S3 (see section below) closes this gap retroactively.
- **No video transcription plugin**: 2.4 itself calls this optional ("provided a transcription plugin is available") — no transcription engine exists yet, so there is no rule for video formats in this session.

## Universal PDF/A Conversion (5.6, since P7-S3)

Until P5-S2, `PdfArchiveRenderer` only produced an archive copy for already-PDF documents (pure `pypdf` metadata tagging) — the rationale at the time: "LibreOffice headless not reliably available." This assumption was explicitly **corrected** in P7-S3: LibreOffice is in fact installed on the target host (just not on `PATH`) and was verified live against real conversions. User directive from P7-S3 (records disposal, 5.6): **all common document types must be archivable**, PDF/A preferred, plain PDF acceptable as a fallback — no silent "original format" fallback for documents that fail to convert.

`PdfArchiveRenderer.render()` dispatches by source format into three paths:

1. **Already PDF** — unchanged, the existing `pypdf` tagging logic (info metadata, PDF structure tree rewritten).
2. **Raster images** (`.png`/`.jpg`/`.bmp`/`.tiff`/...) — directly via **Pillow** (`Image.save(buffer, format="PDF")`), no new library, faster than a LibreOffice subprocess for the simplest case.
3. **Office/text formats** (`.docx`/`.pptx`/`.xlsx`/`.odt`/`.ods`/`.odp`/`.rtf`/`.txt`) — `renderers/_libreoffice.py` invokes `soffice --headless --convert-to pdf:writer_pdf_Export:{"SelectPdfVersion":{"type":"long","value":1}}` via subprocess (PDF/A-1b export filter, embedded fonts/XMP metadata). Every call gets an isolated `-env:UserInstallation=file://<tmp-profile>` to avoid "another instance running" lock conflicts on parallel calls. Binary path resolution first via `PATH` (`soffice`/`libreoffice`), then known absolute paths (`/opt/libreoffice*/program/soffice`) — covers both the slim Docker image (`apt-get install libreoffice-writer libreoffice-calc libreoffice-impress`) and this dev environment.

If a conversion technically fails (missing binary path, timeout), `_libreoffice.ConversionError` is raised — the rendition gets `status="failed"` with `error_message`, no silent fallback. LibreOffice itself is surprisingly permissive when parsing corrupted input (it even tries to import garbage data with the wrong extension as text, rather than reliably failing) — the only reliably testable failure case is a missing/wrong binary path.

**Still not independently veraPDF-validated**: LibreOffice's own PDF/A export filter is technically more conformant than the previous pure `pypdf` post-processing, but this remains an unchanged limitation, now merely communicated transparently.

## Follow-up Effect: OCR Full Text as a `substitute_text` Rendition (P5-S3, 2.4/3.9)

In addition to the `document.>` subscription, rendering-service has consumed `ocr.completed` from the new `ocr-service` since P5-S3 (its own durable name `rendering-service-ocr`, separate from the `document.>` subscription, since both live on different streams — the same `event_bus` client, two subscriptions). For every `ocr.completed` with status `ready`/`needs_review`:

1. Checks via `repository.get_rendition_optional()` whether a rendition already exists for `(document_id, version_number, "substitute_text")` (e.g. generated by `DocxTextExtractionRenderer`/`PptxTextExtractionRenderer`) — if so, does nothing (no duplicate, no unnecessary work).
2. Fetches the OCR full text via HTTP from the OCR Service (`GET /ocr-results/{document_id}:{version_number}`, new `OcrServiceClient`) — `ocr.completed` events deliberately carry only status fields (`version_number`, `status`, `engine`, `average_confidence`), not the potentially large full text itself, to keep NATS payloads and the audit hash chain small.
3. Creates a `substitute_text` rendition if the text is non-empty (`pipeline.process_ocr_text()`) — the same rendition type string as `DocxTextExtractionRenderer`/`PptxTextExtractionRenderer`, consistently treated as "the text-based rendition of this version, regardless of origin."

This closes the gap deliberately left open in P5-S2: scanned/image-based documents now also get a text-based rendition once OCR completes — and, as an intended side effect, so do PDFs with a real text layer, for which previously only the `pdf_archive` archive copy existed, no text extraction.

**Since P5b-S5, tolerant of a missing `ocr-service`** ([ADR 0016](../adr/0016-ocr-configurability-compose-profile-and-live-settings.md)): `ocr-service` is now optionally deployable via a Docker Compose profile (`ocrEnabled`). A legacy `ocr.completed` event from when OCR was still running would otherwise throw an unhandled exception during the HTTP lookup and be redelivered endlessly without ever being processable — `get_full_text()` is therefore now wrapped in `try`/`except` (the same pattern search-service already had beforehand).

## Watermarking as an On-Demand Function, Not an Automatic Rule (3.7)

Unlike renditions, `POST /render/watermark` is deliberately **not** an automatic pipeline step and is **not** persisted: a watermark (e.g. "CONFIDENTIAL", a recipient name on an export) is typically a deliberate one-off action for a specific occasion, not a default step for every uploaded PDF. The implementation (`watermark.py`, reportlab + pypdf) is deliberately kept simple: a single diagonal, semi-transparent text stamp on every page, no position/color/repetition configuration.

## Document Redaction: Genuine Content Removal (14.2, Post-Roadmap Phase 31 Session 4, [ADR 0115](../adr/0115-document-redaction-genuine-content-removal.md))

`redaction.py` is a deliberately different PDF-mutation technique from `watermark.py`: it uses **PyMuPDF**
(`fitz`), not pypdf/reportlab, because only PyMuPDF's redaction API (`page.add_redact_annot()` +
`page.apply_redactions()`) actually **removes** the covered text/graphics from the content stream —
`watermark.py`'s `page.merge_page()` only overlays a stamp on top of unchanged content. This distinction
is why redaction needs its own module rather than extending the watermark one. `pymupdf` was already a
proven dependency in this project (`ocr-service`, since early phases) — this session adds the identical
version constraint here.

- **`get_page_count()`/`render_page_image()`**: any PDF page can be rasterized this way (`page.get_pixmap()`),
  regardless of whether it has a native text layer or is a scan — unlike `ocr-service`'s page images
  (`OcrResult.page_image_storage_key`), which only exist for the Tesseract-processed subset. Used by
  `document-service`'s redaction-preview proxy endpoints to feed the redaction UI.
- **`apply_redactions()`**: `x`/`y`/`width`/`height` are fractions (0..1) of each page's own dimensions —
  resolution-independent, converted to PDF-point rectangles per page. PyMuPDF's coordinate system is
  top-left-origin, y-grows-downward (verified empirically, matching `ocr_service.text_layer`'s existing
  assumption for the same reason) — no axis mirroring needed against the frontend's percentage-based
  region coordinates.
- Consequence for downstream services: since content is genuinely gone, a redacted copy's later OCR
  pass (native-text-layer extraction or Tesseract) naturally omits the removed text — no separate
  "exclude from search index" mechanism was needed anywhere (see ADR 0115).

## PDF Export: Two-Pass Merge/Bookmark/Sub-Numbering (Post-Roadmap Phase 28, [ADR 0107](../adr/0107-pdf-export-two-pass-merge-subnumbering.md))

`export_pdf.py` owns the PDF export feature's actual mechanics, reusing `watermark.py`'s overlay-merge idiom for footer stamping (a small corner label instead of a diagonal stamp):

- **Pass A** (`build_document_export`, per document): the converted document PDF + a reportlab-rendered export-history table (`render_history_pdf`, fed by document-service from an `audit-service` query, see [ADR 0108](../adr/0108-export-history-as-audit-service-query.md)) are concatenated in the caller-specified order, then stamped with a **local** "i/N" footer, N = this document's own page count — final and stable regardless of whether/how the document is later embedded in a folder export.
- **Pass B** (`build_folder_export`, folder export only): a table-of-contents section is rendered first (a throwaway probe determines its own page count before the real offsets are known), cumulative page offsets are computed from each document's already-known Pass-A page count, then a **second, independent** global "j/total" footer (different vertical position than the local one) plus a `pypdf.PdfWriter.add_outline_item()` bookmark per document are added.

`POST /render/convert-to-pdf` (on-demand, no persisted `Rendition`) reuses `PdfArchiveRenderer`'s format dispatch directly rather than duplicating it — the same conversion logic the automatic pipeline uses for the `pdf_archive` rendition type. `POST /render/export/document`/`POST /render/export/folder` compose `export_pdf.py`'s functions with that same conversion step.

## Backend Integration

- **Document Service** (3.1): `GET /documents/{id}/versions/{n}` (metadata) and `.../content` (original bytes) — no direct access to its schema/storage key.
- **Storage Service** (3.6): `PUT`/`GET /objects/renditions/{document_id}/{version_number}/{rendition_type}` — persistence of all results.
- **OCR Service** (3.9, since P5-S3): `GET /ocr-results/{document_id}:{version_number}` — full-text lookup for the follow-up effect above.

## Events

| Event | Payload | When |
|---|---|---|
| `rendering.completed` | `{version_number, rendition_type, target_filename, status: "ready"\|"failed", error}` | After **every** applied rule — even on `status="failed"`, so the audit trail (5.3) shows failures without gaps too. Also after the OCR follow-up effect (`rendition_type="substitute_text"`). |

Additionally consumed (not published): `ocr.completed` from the OCR Service (see follow-up effect above).

The Audit Service has consumed `rendering.>` since this session (see `docs/services/audit-service.md`).

## Self-Registration (Concept 3.2a)

Registers itself with the registry at startup via `dms-registry-client` — opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Sensors (Concept 10.1)

None yet — follows in Phase 11.

## Tests

- `uv run pytest services/rendering-service/tests` (**59 tests**, +13 since **Post-Roadmap Phase 28** ([ADR 0107](../adr/0107-pdf-export-two-pass-merge-subnumbering.md)): `test_export_pdf.py` (6, `build_document_export`/`build_folder_export`/`render_history_pdf` — local vs. global footer stability, TOC offsets, bookmark page indices) and `test_api.py` (7, `/render/convert-to-pdf`/`/render/export/document`/`/render/export/folder` incl. format-rejection and mismatched-titles/files cases); before that 46, previously 44, +2 since **Post-Roadmap Phase
  20 Session 7** ([ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)): `document_id`
  optional in `GET /renditions` — a repository test and an API test confirm the cross-document
  call with the `status` filter, without a `422` being returned; before that 44, +11 since **Post-Roadmap Phase 20 Session 4** — backoff behavior, `list_due_for_retry` filtering, `reset_for_retry` regression test, `process_version`'s `failed_permanent` path, new `/retry` endpoint, new `test_main.py` for `_run_retry_tick`, see [ADR 0080](../adr/0080-rendering-ocr-service-retry-backoff-failed-permanent.md)): renderer behavior against real, in-memory generated files (real PNG/`.docx`/`.pptx`/PDF, no fixture files, no mocks), repository (upsert/overwrite/filter), pipeline (`process_version` directly against the real running Document/Storage Service, incl. error isolation on a corrupted PDF; since P5-S3 additionally `process_ocr_text` for the follow-up effect), API (`/renditions` endpoints, watermarking incl. rejection on an invalid PDF), consumer integration (a real NATS event `document.created`/`document.version.created` triggers real rendering; since P5-S3 additionally `test_ocr_consumer.py` for the `ocr.completed` dispatch incl. duplicate check, with a fake `OcrServiceClient` instead of a real OCR service call; since P5b-S5 additionally a regression test with an `OcrServiceClient` that simulates a connection error — the handler must not crash in that case).
- Verified live against the real stack via the API gateway: upload → automatic thumbnail generation → download of the rendition (correctly downscaled real PNG) → `rendering.completed` visible in the audit trail, hash chain still intact. Since P5-S3, additionally: PDF with a text layer uploaded → OCR Service produces a `native_text_layer` result → rendering-service automatically produces a `substitute_text` rendition with exactly the same full text. Since P7-S3, additionally: a `.txt` file and a real `.docx` file (generated via `python-docx`) converted live via `soffice --headless` into a valid PDF/A-tagged PDF.
- Since P7-S3, additionally `_libreoffice.py` tests (binary path resolution incl. the failure case of a missing binary via `monkeypatch` on `_BINARY_CANDIDATES`) as well as extended `PdfArchiveRenderer` tests for image/office formats.

## Open Points

- **Image-based/scanned documents**: served since P5-S3 via the follow-up effect (text) — a real image rendition (thumbnail) for scanned/image-based PDFs still does not originate in rendering-service itself, only as OCR's own page image (see `docs/services/ocr-service.md`).
- **PDF/A still not ISO 19005 validated** (see above, since P7-S3 at least via LibreOffice's own export filter instead of pure `pypdf` tagging) — an independent veraPDF check is not part of this service.
- **Larger Docker image** due to the LibreOffice installation (since P7-S3) — a deliberate trade-off for the required format coverage (5.6), no way around it without proprietary cloud APIs.
- **No video transcription plugin**: optional per the concept itself, no engine available.
- ~~**No cleanup of failed renditions**~~ — **fixed in Post-Roadmap Phase 20 Session 4** ([ADR 0080](../adr/0080-rendering-ocr-service-retry-backoff-failed-permanent.md)): automatic retry with full-jitter backoff up to `max_rendering_attempts`, after that `failed_permanent` + manual restart via `POST .../retry` (targeted only at the affected renderer).
- ~~No authorization~~ — **fixed in Post-Roadmap Phase 19 Session 8** ([ADR 0073](../adr/0073-ocr-rendering-virus-scan-rbac.md)): all endpoints now check `rendering.read`/`rendering.write` via `permission-service`. Still open: per 2.4, renditions should inherit the same permissions as the original (fine-grained, document-specific) — the new check is a coarse, service-wide `read`/`write`, not inheritance of the concrete document permission.
- **Watermark endpoint deliberately minimal**: fixed diagonal stamp, no position/repetition/color configuration.
