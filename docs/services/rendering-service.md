# rendering-service

**Verantwortung:** Rendering/Preview + Ersatzdarstellungen (Konzept 3.7/2.4) — erzeugt automatisch dauerhaft persistierte Vorschauen/Ersatzdarstellungen für neue Dokumentversionen und stellt On-Demand-Rendering-Funktionen (Wasserzeichen) bereit.
**Konzept-Referenz:** 3.7, 2.4
**Eigenes Postgres-Schema:** `rendering` (`rendition`).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/renditions?document_id=...&version_number=...` | Erzeugte Ersatzdarstellungen/Vorschauen zu einer Version (`version_number` optional — ohne Angabe: alle Versionen des Dokuments) |
| `GET` | `/renditions/{id}` | Einzelne Ersatzdarstellung (Metadaten) — 404 bei unbekannter `id` |
| `GET` | `/renditions/{id}/content` | Bytes der Ersatzdarstellung (Proxy auf den Storage Service) — 404 bei unbekannter `id`, 409 bei Status `failed` |
| `POST` | `/render/watermark` | Multipart (`file`: PDF, `text`) → On-Demand-Wasserzeichen, liefert das gestempelte PDF direkt zurück, **ohne** es zu persistieren |
| `GET` | `/healthz` | Health-Check |

`id` einer Ersatzdarstellung ist ein natürlicher Schlüssel `{document_id}:{version_number}:{rendition_type}` (siehe Datenmodell) — kein zufälliger UUID.

## Automatische Ersatzdarstellungs-Pipeline (2.4) statt Cache

Der Rendering Service konsumiert `document.created` (erste Version, `version_number` implizit `1`) und `document.version.created` (Check-in, `version_number` im Payload) vom Document Service — beide docken **nach** dem Scan-Gating aus P5-S1 an, da Document Service diese Events erst nach erfolgreichem Virenscan und erfolgreichem Schreiben publiziert (ADR 0010). Für jede neue Version:

1. Metadaten (`filename`, `content_type`) und Originalinhalt werden über die **HTTP-API des Document Service** bezogen (`GET .../versions/{n}` bzw. `.../versions/{n}/content`) — der Rendering Service kennt weder dessen Datenmodell noch den content-addressierten Storage-Key direkt (3.1).
2. Alle Regeln, deren Quellformat passt (`renderers/__init__.py`, siehe Tabelle unten), werden unabhängig voneinander angewendet — schlägt eine fehl (z. B. korruptes `.pdf`), werden die übrigen trotzdem erzeugt; die fehlgeschlagene Regel wird mit `status="failed"` und `error_message` festgehalten statt die ganze Verarbeitung abzubrechen.
3. Jedes Ergebnis wird dauerhaft über den **Storage Service** abgelegt (`renditions/{document_id}/{version_number}/{rendition_type}`) — **kein** Cache/Redis/In-Memory, wie in 2.4 explizit gefordert: Ausfallsicherheit darf nicht von der Funktionsfähigkeit desselben Renderers abhängen, der sie eigentlich absichern soll.
4. Erneutes Verarbeiten derselben Version (z. B. NATS-Redelivery) überschreibt die vorhandene Zeile (natürlicher Primärschlüssel), statt Duplikate anzuhäufen.

Ein einziges breites Subject-Abo (`document.>`, nicht zwei Einzel-Subscriptions) mit In-Handler-Dispatch nach `event_type` — dasselbe Muster wie `permission-service/structure_consumer.py`, da ein JetStream-Durable-Consumer-Name pro abonniertem Subject reserviert wäre. Andere `document.>`-Events (Metadaten-Update, Löschung, Force-Unlock) lösen kein Rendering aus.

**Rückwirkende Verarbeitung beim ersten Start**: Da kein `deliver_new` gesetzt ist (wie bei `permission-service`/`audit-service`), holt ein frischer Durable Consumer beim allerersten Start die komplette bisherige Event-Historie nach — bereits vor dieser Session hochgeladene Dokumente werden also rückwirkend mit Ersatzdarstellungen versehen, sobald der Service erstmals läuft. Gewolltes Verhalten (Backfill), keine Race Condition.

## Ersatzdarstellungs-Regeln (Plugin-Prinzip wie Storage-Backends, 3.3/3.8)

| Quellformat | Ziel | Renderer | `rendition_type` |
|---|---|---|---|
| Rasterbilder (`image/*`) | PNG-Thumbnail, max. 256×256 | `ThumbnailRenderer` (Pillow) | `thumbnail` |
| `.docx` | `.txt`-Textextraktion | `DocxTextExtractionRenderer` (python-docx) | `substitute_text` |
| `.pptx` | `.txt`-Textextraktion je Folie | `PptxTextExtractionRenderer` (python-pptx) | `substitute_text` |
| `.pdf` | PDF-Archivkopie (Best-Effort-Metadaten-Tagging) | `PdfArchiveRenderer` (pypdf) | `pdf_archive` |

Neue Regeln werden ergänzt, indem eine weitere `Renderer`-Klasse in `renderers/__init__.py` registriert wird, ohne bestehenden Code zu ändern (`RENDERERS`-Liste, `select_renderers()`).

**Bewusste Abweichungen vom Konzept-Beispieltext, dieselbe Abwägung wie ClamdEngine vs. EicarSignatureEngine in P5-S1/ADR 0010** — echte, ohne externe Systemabhängigkeit erreichbare Funktionalität statt eines Platzhalters:

- **`.pptx -> .txt` statt `.pptx -> .pdf`**: eine echte Office-zu-PDF-Konvertierung bräuchte eine externe Rendering-Komponente (z. B. LibreOffice headless), die in dieser Umgebung nicht verlässlich/schnell verfügbar ist. Textextraktion ist trotzdem eine vollwertige Ersatzdarstellung im Sinne von 2.4.
- **PDF/A-Konvertierung ist Best-Effort-Tagging, nicht ISO-19005-validiert**: `PdfArchiveRenderer` kopiert den PDF-Strukturbaum neu und setzt Info-Metadaten, prüft aber keine Font-Einbettung/Farbräume — echte PDF/A-Konformität bräuchte Ghostscript/veraPDF.
- **Bildbasierte/gescannte Dokumente wurden in P5-S2 bewusst nicht bedient** (sie hätten einen OCR-Textlayer als Grundlage gebraucht, 3.9) — der Nachzieheffekt aus P5-S3 (siehe Abschnitt unten) schließt diese Lücke nachträglich.
- **Kein Video-Transkriptions-Plugin**: laut 2.4 selbst optional ("sofern ein Transkriptions-Plugin verfügbar ist") — es existiert noch keine Transkriptions-Engine, daher keine Regel für Video-Formate in dieser Session.

## Nachzieheffekt: OCR-Volltext als `substitute_text`-Rendition (P5-S3, 2.4/3.9)

Zusätzlich zum `document.>`-Abo konsumiert rendering-service seit P5-S3 `ocr.completed` vom neuen `ocr-service` (eigener Durable-Name `rendering-service-ocr`, getrennt vom `document.>`-Abo, da beide auf unterschiedlichen Streams liegen — derselbe `event_bus`-Client, zwei Subscriptions). Für jedes `ocr.completed` mit `status` `ready`/`needs_review`:

1. Prüft per `repository.get_rendition_optional()`, ob für `(document_id, version_number, "substitute_text")` bereits eine Rendition existiert (z. B. durch `DocxTextExtractionRenderer`/`PptxTextExtractionRenderer` erzeugt) — falls ja, nichts tun (kein Duplikat, keine unnötige Arbeit).
2. Holt den OCR-Volltext per HTTP vom OCR Service nach (`GET /ocr-results/{document_id}:{version_number}`, neuer `OcrServiceClient`) — `ocr.completed`-Events tragen bewusst nur Statusfelder (`version_number`, `status`, `engine`, `average_confidence`), nicht den potenziell großen Volltext selbst, um NATS-Payloads und die Audit-Hashkette klein zu halten.
3. Legt bei nicht-leerem Text eine `substitute_text`-Rendition an (`pipeline.process_ocr_text()`) — derselbe Rendition-Typ-String wie bei `DocxTextExtractionRenderer`/`PptxTextExtractionRenderer`, konsistent als "die textbasierte Ersatzdarstellung dieser Version, gleich welcher Herkunft".

Schließt damit die in P5-S2 bewusst offen gelassene Lücke: gescannte/bildbasierte Dokumente bekommen jetzt ebenfalls eine textbasierte Ersatzdarstellung, sobald OCR abgeschlossen ist — und, als beabsichtigter Nebeneffekt, auch PDFs mit echtem Textlayer, für die es bislang nur die `pdf_archive`-Archivkopie gab, keine Textextraktion.

**Seit P5b-S5 tolerant gegenüber fehlendem `ocr-service`** ([ADR 0016](../adr/0016-ocr-configurability-compose-profile-and-live-settings.md)): `ocr-service` ist jetzt per Docker-Compose-Profil optional deploybar (`ocrEnabled`). Ein `ocr.completed`-Alt-Event aus der Zeit, als OCR noch lief, würde beim HTTP-Nachschlag sonst eine unbehandelte Exception werfen und endlos redeliver-t werden, ohne je verarbeitbar zu sein — `get_full_text()` ist deshalb jetzt in `try`/`except` gefasst (gleiches Muster wie search-service, das dies bereits vorher hatte).

## Wasserzeichen als On-Demand-Funktion, nicht als automatische Regel (3.7)

Anders als die Ersatzdarstellungen ist `POST /render/watermark` bewusst **kein** automatischer Pipelineschritt und wird **nicht** persistiert: ein Wasserzeichen (z. B. "VERTRAULICH", ein Empfängername bei einem Export) ist typischerweise eine bewusste Einzelaktion für einen konkreten Anlass, kein Standardschritt für jedes hochgeladene PDF. Die Implementierung (`watermark.py`, reportlab + pypdf) ist bewusst einfach gehalten: ein einziger diagonaler, halbtransparenter Textstempel über jede Seite, keine Positions-/Farb-/Wiederholungs-Konfiguration.

## Anbindung an das Backend

- **Document Service** (3.1): `GET /documents/{id}/versions/{n}` (Metadaten) und `.../content` (Originalbytes) — kein direkter Zugriff auf dessen Schema/Storage-Key.
- **Storage Service** (3.6): `PUT`/`GET /objects/renditions/{document_id}/{version_number}/{rendition_type}` — Persistenz aller Ergebnisse.
- **OCR Service** (3.9, seit P5-S3): `GET /ocr-results/{document_id}:{version_number}` — Volltext-Nachschlag für den Nachzieheffekt oben.

## Events

| Event | Payload | Wann |
|---|---|---|
| `rendering.completed` | `{version_number, rendition_type, target_filename, status: "ready"\|"failed", error}` | Nach **jeder** angewendeten Regel — auch bei `status="failed"`, damit der Audit-Trail (5.3) auch Fehlschläge lückenlos zeigt. Auch nach dem OCR-Nachzieheffekt (`rendition_type="substitute_text"`). |

Zusätzlich konsumiert (nicht publiziert): `ocr.completed` vom OCR Service (siehe Nachzieheffekt oben).

Der Audit Service konsumiert `rendering.>` seit dieser Session (siehe `docs/services/audit-service.md`).

## Selbst-Registrierung (Konzept 3.2a)

Meldet sich beim Start über `dms-registry-client` selbst bei der Registry an — Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

- `uv run pytest services/rendering-service/tests`: Renderer-Verhalten gegen echte, in-memory erzeugte Dateien (echtes PNG/`.docx`/`.pptx`/PDF, keine Fixture-Dateien, keine Mocks), Repository (Upsert/Überschreiben/Filter), Pipeline (`process_version` direkt gegen den echten laufenden Document/Storage Service, inkl. Fehler-Isolation bei korruptem PDF; seit P5-S3 zusätzlich `process_ocr_text` für den Nachzieheffekt), API (`/renditions`-Endpunkte, Wasserzeichen inkl. Ablehnung bei ungültigem PDF), Consumer-Integration (echtes NATS-Event `document.created`/`document.version.created` löst echtes Rendering aus; seit P5-S3 zusätzlich `test_ocr_consumer.py` für den `ocr.completed`-Dispatch inkl. Duplikatsprüfung, mit einem Fake-`OcrServiceClient` statt eines echten OCR-Service-Aufrufs; seit P5b-S5 zusätzlich ein Regressionstest mit einem `OcrServiceClient`, der einen Verbindungsfehler simuliert — der Handler darf dabei nicht crashen).
- Live gegen den echten Stack über das API-Gateway verifiziert: Upload → automatische Thumbnail-Erzeugung → Download der Ersatzdarstellung (korrekt herunterskaliertes echtes PNG) → `rendering.completed` im Audit-Trail sichtbar, Hash-Kette weiterhin intakt. Seit P5-S3 zusätzlich: PDF mit Textlayer hochgeladen → OCR Service erzeugt `native_text_layer`-Ergebnis → rendering-service erzeugt automatisch eine `substitute_text`-Rendition mit exakt demselben Volltext.

## Offene Punkte

- **Bildbasierte/gescannte Dokumente**: seit P5-S3 über den Nachzieheffekt (Text) bedient — eine echte Bild-Ersatzdarstellung (Thumbnail) für gescannte/bildbasierte PDFs entsteht weiterhin nicht in rendering-service selbst, sondern nur als OCR-eigenes Seitenbild (siehe `docs/services/ocr-service.md`).
- **`.pptx -> .txt` statt `.pptx -> .pdf`, PDF/A nicht ISO-19005-validiert**: siehe Begründung oben — echte Office-/PDF-A-Konvertierung bräuchte externe Systemkomponenten (LibreOffice/Ghostscript), die hier nicht verfügbar sind.
- **Kein Video-Transkriptions-Plugin**: laut Konzept selbst optional, keine Engine vorhanden.
- **Keine Bereinigung fehlgeschlagener Renditions**: eine dauerhaft `status="failed"` bleibende Zeile wird nicht automatisch erneut versucht (kein Retry-Mechanismus).
- **Keine Autorisierung** (wie bei allen bisherigen Services): Gateway prüft nur Token-Gültigkeit, keine Rollenprüfung; Ersatzdarstellungen erben laut 2.4 dieselben Berechtigungen wie das Original, was hier (mangels durchgängiger Autorisierung im Gesamtsystem, siehe `PROGRESS.md`) noch nicht technisch durchgesetzt wird.
- **Wasserzeichen-Endpunkt bewusst minimal**: fester diagonaler Stempel, keine Positions-/Wiederholungs-/Farbkonfiguration.
