# rendering-service

Rendering/preview + renditions (Concept 3.7/2.4): automatically generates
previews/renditions for new document versions (after `document.created`/
`document.version.created`, which the Document Service only publishes *after*
a successful virus scan, ADR 0010) and provides on-demand rendering functions.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/renditions?document_id=...&version_number=...` | Generated renditions/previews for a version (without `version_number`: all versions) |
| `GET` | `/renditions/{id}` | Single rendition (metadata) |
| `GET` | `/renditions/{id}/content` | Rendition bytes (proxy to the Storage Service) |
| `POST` | `/render/watermark` | Multipart: `file` (PDF), `text` — on-demand watermark, **not** persisted |
| `GET` | `/healthz` | Health check |

Details/schema: see `../../docs/services/rendering-service.md`.

## Rendition Rules (2.4/3.7, plugin principle like storage backends)

Automatically applied to every new document version (`renderers/__init__.py`
registers the active rules):

| Source format | Target | Renderer |
|---|---|---|
| Raster images (`image/*`) | PNG thumbnail (max. 256×256) | `ThumbnailRenderer` (Pillow) |
| `.docx` | `.txt` text extraction | `DocxTextExtractionRenderer` (python-docx) |
| `.pptx` | `.txt` text extraction per slide | `PptxTextExtractionRenderer` (python-pptx) — deliberately deviates from the concept example `.pptx -> .pdf`, see module docstring |
| `.pdf` | PDF archive copy (best-effort tagging) | `PdfArchiveRenderer` (pypdf) — **not** an ISO-19005-validated PDF/A |

Formats without a matching rule (including image-based/scanned documents that
need OCR) are deliberately not handled in this session — a follow-on effect
of P5-S3. Persistence of all results exclusively via the Storage Service
(2.4: no cache).

## Registry Registration (since P4-S1)

Registers itself with the registry on startup via `dms-registry-client` — opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Running Locally

```bash
cd infra && docker compose up -d postgres nats storage-service document-service rendering-service
curl localhost:8011/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats storage-service document-service && cd ..
uv run pytest services/rendering-service/tests
```
