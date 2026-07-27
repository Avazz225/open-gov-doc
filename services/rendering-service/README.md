# rendering-service

Rendering/Preview + Ersatzdarstellungen (Konzept 3.7/2.4): erzeugt automatisch
Vorschauen/Ersatzdarstellungen für neue Dokumentversionen (nach `document.created`/
`document.version.created`, die der Document Service erst *nach* erfolgreichem
Virenscan publiziert, ADR 0010) und stellt On-Demand-Rendering-Funktionen bereit.

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/renditions?document_id=...&version_number=...` | Erzeugte Ersatzdarstellungen/Vorschauen zu einer Version (ohne `version_number`: alle Versionen) |
| `GET` | `/renditions/{id}` | Einzelne Ersatzdarstellung (Metadaten) |
| `GET` | `/renditions/{id}/content` | Bytes der Ersatzdarstellung (Proxy auf den Storage Service) |
| `POST` | `/render/watermark` | Multipart: `file` (PDF), `text` — On-Demand-Wasserzeichen, **nicht** persistiert |
| `GET` | `/healthz` | Health-Check |

Details/Schema: siehe `../../docs/services/rendering-service.md`.

## Ersatzdarstellungs-Regeln (2.4/3.7, Plugin-Prinzip wie Storage-Backends)

Automatisch bei jeder neuen Dokumentversion angewendet (`renderers/__init__.py`
registriert die aktiven Regeln):

| Quellformat | Ziel | Renderer |
|---|---|---|
| Rasterbilder (`image/*`) | PNG-Thumbnail (max. 256×256) | `ThumbnailRenderer` (Pillow) |
| `.docx` | `.txt`-Textextraktion | `DocxTextExtractionRenderer` (python-docx) |
| `.pptx` | `.txt`-Textextraktion je Folie | `PptxTextExtractionRenderer` (python-pptx) — weicht bewusst vom Konzept-Beispiel `.pptx -> .pdf` ab, siehe Modul-Docstring |
| `.pdf` | PDF-Archivkopie (Best-Effort-Tagging) | `PdfArchiveRenderer` (pypdf) — **kein** ISO-19005-validiertes PDF/A |

Formate ohne passende Regel (inkl. bildbasierter/gescannter Dokumente, die
OCR brauchen) werden in dieser Session bewusst nicht bedient — Nachzieheffekt
von P5-S3. Persistenz aller Ergebnisse ausschließlich über den Storage
Service (2.4: kein Cache).

## Registry-Registrierung (seit P4-S1)

Meldet sich beim Start über `dms-registry-client` selbst bei der Registry an — Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats storage-service document-service rendering-service
curl localhost:8011/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats storage-service document-service && cd ..
uv run pytest services/rendering-service/tests
```
