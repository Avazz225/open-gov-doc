# 0107 — PDF export: two-pass merge/bookmark/sub-numbering, ExportConfig default+override

**Status:** accepted (P28-S1/S3/S4/S5/S6, see `IMPLEMENTATION_PLAN.md`)
**Context:** new post-roadmap feature (PDF export with export history, combined folder export), affects `services/rendering-service/`, `services/document-service/`, `apps/user-ui/`, precedent in `services/rendering-service/src/rendering_service/watermark.py`, `services/archival-service/` (ADR 0078)

## Decision

A new "Export" action, alongside the existing "Download", produces a PDF of
a document plus its export history in a configurable order
(`before`/`after`), and — for a whole folder — a single combined PDF with a
table of contents, bookmarks, and per-document page numbering stable
against later export-history growth.

**Two-pass pipeline**, built on the exact reportlab-overlay-merged-via-pypdf
idiom `watermark.py`'s `add_text_watermark` already established (only a
small corner-label footer instead of a diagonal stamp):

1. **Pass A** (`rendering_service/export_pdf.py::build_document_export`,
   per document, identical whether the document is exported alone or as
   part of a folder): the document's own PDF (converted via
   `PdfArchiveRenderer`, the same dispatch `/render/convert-to-pdf` uses)
   and its export-history PDF (`render_history_pdf`, a plain reportlab
   table fed from `audit-service`'s `document.exported` event log) are
   concatenated in the configured order, then stamped with a **local**
   footer "i/N", N = this document's own page count. This local stamp is
   final the moment Pass A runs — it never changes later, regardless of how
   many further exports get appended to this document's history or whether
   the document later gets folded into a combined export.
2. **Pass B** (`build_folder_export`, folder export only): a
   table-of-contents section is rendered first (a throwaway probe run
   determines its own page count, since it depends on the document count,
   not on offsets it will display), cumulative page offsets are computed
   from each document's already-known Pass-A page count, then a **second,
   independent** global footer "j/total" is stamped (different vertical
   offset from the local one, so neither overlaps) plus a `pypdf` outline
   item (bookmark) per document via `PdfWriter.add_outline_item()`.

`rendering-service` owns all PDF mechanics end to end (new endpoints
`POST /render/export/document` and `POST /render/export/folder`);
`document-service` only orchestrates — fetches content, queries
`audit-service` for history, calls rendering-service, publishes
`document.exported`. Neither pypdf nor reportlab needed as new
dependencies — both were already present in rendering-service.

**Order configuration** (`history_position`): an installation-wide default
(`ExportConfig`, a single-row table, default `"after"`) with an optional
per-request `?history_position=` override — the same default+override
layering already used by `ShareLinkConfig`/`AuditTraceConfig`.

**Folder export runs as an async job** (`FolderExportJob`,
`POST /folders/{id}/export` returns `202` immediately), since LibreOffice
conversion per contained document plus the merge can take long for a
folder with many documents — polled via `GET /folder-exports/{id}` until
`completed`/`failed_permanent`. Resilience shape is a direct copy of
`ArchivalTransfer`'s (ADR 0078): `attempts`/`next_retry_at` full-jitter
backoff (`dms-retry`, `compute_backoff_seconds`), `failed_permanent` as the
real terminal state instead of a bare `failed`.

## Rationale

- **rendering-service owns PDF mechanics, document-service owns
  orchestration**: matches the existing service boundary (rendering-service
  already does all PDF/rendition work; document-service already
  orchestrates cross-service calls for uploads, e.g. virus-scan-service).
  Keeping merge/bookmark/stamping logic in one place (`export_pdf.py`) also
  means `POST /render/export/folder` can be tested and reasoned about
  without a real document-service round trip.
- **Local footer stamped before the document is known to be part of any
  combined export**: the literal requirement ("page references stay
  accurate even as export history changes") is satisfied by construction —
  Pass A's footer only ever depends on that document's own two sections,
  computed once, never revisited. A combined export's later, larger,
  independent global footer is explicitly a *second* number on the page,
  not a replacement.
- **Dependency-injected clients in `_build_document_export_pdf`/
  `_run_folder_export_tick`** (`storage`/`rendering_client`/`audit_client`
  passed explicitly) rather than read from `app.state`, unlike most poll
  loops in this service: discovered during testing that httpx clients
  constructed on one asyncio event loop cannot be reused from another —
  `TestClient(app)`'s internal portal loop differs from a bare async test
  function's own loop. Matches archival-service's `pipeline.
  run_active_transfers_tick`, which already takes its clients as explicit
  parameters for the same testability reason.
- **`title`/`history` passed as multipart form fields** (JSON-encoded
  `history`) to `/render/export/document` rather than a JSON body:
  rendering-service's existing `/render/watermark`/`/render/convert-to-pdf`
  endpoints are both multipart (`UploadFile` + `Form` fields) — consistent
  with that precedent instead of introducing a second content-type
  convention on the same service.
- **`data` as a dict-of-lists, not a list of tuples, for
  `/render/export/folder`'s `titles` field**: `httpx.AsyncClient.post(data=[...], files=[...])`
  (both list-of-tuples) built a request whose body stream wasn't recognized
  as async-native by httpx, raising `RuntimeError: Attempted to send an
  sync request with an AsyncClient instance` at send time — `data={"titles": [...]}`
  avoids the issue entirely and is httpx's documented way to send a
  repeated form field.

## Consequences

- Folder-export document order follows `Kennzeichen` (reference number)
  when set, falling back to title — `document-service`'s existing
  `list_documents_by_folder` sorts alphabetically by title only, not
  meaningful for a TOC; a new `list_documents_for_folder_export` sorts by
  `(Kennzeichen or "", title)` instead, in Python (no new query needed).
- The folder-export poll interval (`folder_export_poll_interval_seconds`,
  default 30s) plus real conversion time means a combined export can take
  noticeably longer than a single-document one to become available — the
  frontend polls `GET /folder-exports/{id}` every 3s and downloads
  automatically once `completed`, so this is a wait, not a manual refresh,
  but a folder with many documents will visibly take longer.
- **`DMS_RENDERING_SERVICE_BASE_URL`/`DMS_AUTH_SERVICE_BASE_URL` had to be
  added to `document-service`'s `docker-compose.yml` environment** — caught
  during live verification (a `502` with no matching rendering-service log
  entry at all revealed the calls never left the container, since the
  `Settings` class default of `http://localhost:8011` resolves to the
  *document-service* container's own loopback, not rendering-service's).
  Same class of gap as the `DMS_MONITORING_SERVICE_BASE_URL` omission found
  during the HTTP-sensor rollout — a reminder that a new cross-service
  client needs its base URL wired into `docker-compose.yml` explicitly,
  the `Settings` default alone is only a local/no-Docker fallback.
- No PDF/A validation, no veraPDF check anywhere in this pipeline — same
  deliberate limitation already documented for `PdfArchiveRenderer` itself.
