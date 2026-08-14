# rendering-service

**Verantwortung:** Rendering/Preview + Ersatzdarstellungen (Konzept 3.7/2.4) — erzeugt automatisch dauerhaft persistierte Vorschauen/Ersatzdarstellungen für neue Dokumentversionen und stellt On-Demand-Rendering-Funktionen (Wasserzeichen) bereit.
**Konzept-Referenz:** 3.7, 2.4
**Eigenes Postgres-Schema:** `rendering` (`rendition`).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/renditions?document_id=...&version_number=...&status=...` | Erzeugte Ersatzdarstellungen/Vorschauen zu einer Version (`version_number` optional — ohne Angabe: alle Versionen des Dokuments) — seit **P19-S8** `rendering.read`-gegated. Seit **Post-Roadmap Phase 20 Session 7** ist auch `document_id` optional (vorher Pflicht) und ein neuer `status`-Filter kam dazu — ohne `document_id` liefert dies eine dokumentübergreifende Liste, Grundlage für die neue Admin-UI-Sicht auf `failed_permanent`-Renditions ([ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)) |
| `GET` | `/renditions/{id}` | Einzelne Ersatzdarstellung (Metadaten) — 404 bei unbekannter `id`; seit **P19-S8** `rendering.read`-gegated |
| `GET` | `/renditions/{id}/content` | Bytes der Ersatzdarstellung (Proxy auf den Storage Service) — 404 bei unbekannter `id`, 409 bei Status `failed`/`failed_permanent`; seit **P19-S8** `rendering.read`-gegated |
| `POST` | `/renditions/{id}/retry` | Manueller Neustart einer `failed_permanent`-Ersatzdarstellung (seit **P20-S4**, [ADR 0080](../adr/0080-rendering-ocr-service-retry-backoff-failed-permanent.md)) — `404` bei unbekannter `id`, `409` wenn `status != "failed_permanent"`, sonst sofortiger erneuter Versuch NUR für den betroffenen Renderer; `rendering.write`-gegated |
| `POST` | `/render/watermark` | Multipart (`file`: PDF, `text`) → On-Demand-Wasserzeichen, liefert das gestempelte PDF direkt zurück, **ohne** es zu persistieren; seit **P19-S8** ([ADR 0073](../adr/0073-ocr-rendering-virus-scan-rbac.md)) `rendering.write`-gegated |
| `GET` | `/healthz` | Health-Check |

`id` einer Ersatzdarstellung ist ein natürlicher Schlüssel `{document_id}:{version_number}:{rendition_type}` (siehe Datenmodell) — kein zufälliger UUID.

## Automatische Ersatzdarstellungs-Pipeline (2.4) statt Cache

Der Rendering Service konsumiert `document.created` (erste Version, `version_number` implizit `1`) und `document.version.created` (Check-in, `version_number` im Payload) vom Document Service — beide docken **nach** dem Scan-Gating aus P5-S1 an, da Document Service diese Events erst nach erfolgreichem Virenscan und erfolgreichem Schreiben publiziert (ADR 0010). Für jede neue Version:

1. Metadaten (`filename`, `content_type`) und Originalinhalt werden über die **HTTP-API des Document Service** bezogen (`GET .../versions/{n}` bzw. `.../versions/{n}/content`) — der Rendering Service kennt weder dessen Datenmodell noch den content-addressierten Storage-Key direkt (3.1).
2. Alle Regeln, deren Quellformat passt (`renderers/__init__.py`, siehe Tabelle unten), werden unabhängig voneinander angewendet — schlägt eine fehl (z. B. korruptes `.pdf`), werden die übrigen trotzdem erzeugt; die fehlgeschlagene Regel wird mit `status="failed"` und `error_message` festgehalten statt die ganze Verarbeitung abzubrechen.
3. Jedes Ergebnis wird dauerhaft über den **Storage Service** abgelegt (`renditions/{document_id}/{version_number}/{rendition_type}`) — **kein** Cache/Redis/In-Memory, wie in 2.4 explizit gefordert: Ausfallsicherheit darf nicht von der Funktionsfähigkeit desselben Renderers abhängen, der sie eigentlich absichern soll.
4. Erneutes Verarbeiten derselben Version (z. B. NATS-Redelivery) überschreibt die vorhandene Zeile (natürlicher Primärschlüssel), statt Duplikate anzuhäufen.

Ein einziges breites Subject-Abo (`document.>`, nicht zwei Einzel-Subscriptions) mit In-Handler-Dispatch nach `event_type` — dasselbe Muster wie `permission-service/structure_consumer.py`, da ein JetStream-Durable-Consumer-Name pro abonniertem Subject reserviert wäre. Andere `document.>`-Events (Metadaten-Update, Löschung, Force-Unlock) lösen kein Rendering aus.

### Retry & Backoff (Post-Roadmap Phase 20 Session 4, [ADR 0080](../adr/0080-rendering-ocr-service-retry-backoff-failed-permanent.md))

Ein `status="failed"` (Renderer-Plugin-Fehler) ist seither **nicht mehr sofort terminal**: `attempts`
wird erhöht, `next_retry_at` per `compute_backoff_seconds` (`libs/dms-retry`) gesetzt — ein neuer,
eigenständiger `_rendition_retry_poll_loop` (Intervall `rendering_retry_poll_interval_seconds`, Default
60s) greift fällige Renditions auf. **Besonderheit gegenüber `ocr-service`**: da eine Version MEHRERE
Rendition-Zeilen haben kann (eine je zutreffender Regel), ruft der Retry gezielt NUR den einen
betroffenen Renderer erneut auf (`renderers.get_renderer_by_type`/`pipeline.retry_rendition`), nicht die
gesamte `process_version`-Regelkaskade — sonst würden bereits erfolgreiche Renditions unnötig neu
erzeugt. Erst nach `max_rendering_attempts` (Default 5) erfolglosen Versuchen wechselt `status` auf das
echte Terminalstatus `failed_permanent`, ab dem `POST /renditions/{id}/retry` einen sofortigen manuellen
Neustart erlaubt (setzt zuerst `attempts`/`error_message`/`next_retry_at` zurück, dann ein echter neuer
Versuch).

**Rückwirkende Verarbeitung beim ersten Start**: Da kein `deliver_new` gesetzt ist (wie bei `permission-service`/`audit-service`), holt ein frischer Durable Consumer beim allerersten Start die komplette bisherige Event-Historie nach — bereits vor dieser Session hochgeladene Dokumente werden also rückwirkend mit Ersatzdarstellungen versehen, sobald der Service erstmals läuft. Gewolltes Verhalten (Backfill), keine Race Condition.

## Ersatzdarstellungs-Regeln (Plugin-Prinzip wie Storage-Backends, 3.3/3.8)

| Quellformat | Ziel | Renderer | `rendition_type` |
|---|---|---|---|
| Rasterbilder (`image/*`) | PNG-Thumbnail, max. 256×256 | `ThumbnailRenderer` (Pillow) | `thumbnail` |
| `.docx` | `.txt`-Textextraktion | `DocxTextExtractionRenderer` (python-docx) | `substitute_text` |
| `.pptx` | `.txt`-Textextraktion je Folie | `PptxTextExtractionRenderer` (python-pptx) | `substitute_text` |
| `.ods` | `.txt`-Textextraktion je Tabellenblatt | `OdsTextExtractionRenderer` (odfpy, nachgezogen per Bugfix nach Nutzer-Feedback — `.ods` hatte zuvor überhaupt keinen Renderer) | `substitute_text` |
| `.pdf`/Rasterbilder/Office-Formate (`.docx`/`.pptx`/`.xlsx`/`.odt`/`.ods`/`.odp`/`.rtf`/`.txt`) | Universelle PDF/A-Archivkopie | `PdfArchiveRenderer` (s. u.) | `pdf_archive` |

Neue Regeln werden ergänzt, indem eine weitere `Renderer`-Klasse in `renderers/__init__.py` registriert wird, ohne bestehenden Code zu ändern (`RENDERERS`-Liste, `select_renderers()`).

**Bewusste Abweichungen vom Konzept-Beispieltext, dieselbe Abwägung wie ClamdEngine vs. EicarSignatureEngine in P5-S1/ADR 0010** — echte, ohne externe Systemabhängigkeit erreichbare Funktionalität statt eines Platzhalters:

- **Bildbasierte/gescannte Dokumente wurden in P5-S2 bewusst nicht bedient** (sie hätten einen OCR-Textlayer als Grundlage gebraucht, 3.9) — der Nachzieheffekt aus P5-S3 (siehe Abschnitt unten) schließt diese Lücke nachträglich.
- **Kein Video-Transkriptions-Plugin**: laut 2.4 selbst optional ("sofern ein Transkriptions-Plugin verfügbar ist") — es existiert noch keine Transkriptions-Engine, daher keine Regel für Video-Formate in dieser Session.

## Universelle PDF/A-Konvertierung (5.6, seit P7-S3)

`PdfArchiveRenderer` erzeugte bis P5-S2 nur für bereits-PDF-Dokumente eine Archivkopie (reines `pypdf`-Metadaten-Tagging) — Begründung damals: "LibreOffice headless nicht verlässlich verfügbar". Diese Annahme wurde in P7-S3 explizit **korrigiert**: LibreOffice ist auf dem Zielhost tatsächlich installiert (nur nicht auf `PATH`) und wurde live gegen echte Konvertierungen verifiziert. Nutzervorgabe aus P7-S3 (Aussonderung, 5.6): **alle gängigen Dokumenttypen müssen archivierbar sein**, PDF/A bevorzugt, reines PDF als Fallback akzeptabel — kein stiller "Original-Format"-Fallback für nicht konvertierte Dokumente.

`PdfArchiveRenderer.render()` dispatcht nach Quellformat in drei Pfade:

1. **Bereits PDF** — unverändert die bestehende `pypdf`-Tagging-Logik (Info-Metadaten, PDF-Strukturbaum neu geschrieben).
2. **Rasterbilder** (`.png`/`.jpg`/`.bmp`/`.tiff`/...) — direkt über **Pillow** (`Image.save(buffer, format="PDF")`), keine neue Bibliothek, schneller als ein LibreOffice-Subprozess für den einfachsten Fall.
3. **Office-/Textformate** (`.docx`/`.pptx`/`.xlsx`/`.odt`/`.ods`/`.odp`/`.rtf`/`.txt`) — `renderers/_libreoffice.py` ruft `soffice --headless --convert-to pdf:writer_pdf_Export:{"SelectPdfVersion":{"type":"long","value":1}}` per Subprozess auf (PDF/A-1b-Export-Filter, eingebettete Fonts/XMP-Metadaten). Jeder Aufruf bekommt ein isoliertes `-env:UserInstallation=file://<tmp-profil>`, um "another instance running"-Lock-Konflikte bei Parallelaufrufen zu vermeiden. Binärpfad-Auflösung zuerst über `PATH` (`soffice`/`libreoffice`), sonst bekannte Absolutpfade (`/opt/libreoffice*/program/soffice`) — deckt sowohl das schlanke Docker-Image (`apt-get install libreoffice-writer libreoffice-calc libreoffice-impress`) als auch diese Dev-Umgebung ab.

Scheitert eine Konvertierung technisch (fehlender Binärpfad, Timeout), wirft `_libreoffice.ConversionError` — die Rendition bekommt `status="failed"` mit `error_message`, kein stiller Fallback. LibreOffice selbst ist überraschend permissiv beim Parsen beschädigter Eingaben (versucht auch Datenmüll mit falscher Endung als Text zu importieren, statt zuverlässig fehlzuschlagen) — der einzige zuverlässig testbare Fehlerfall ist ein fehlender/falscher Binärpfad.

**Weiterhin nicht unabhängig veraPDF-validiert**: LibreOffices eigener PDF/A-Export-Filter ist technisch konformer als die vorherige reine `pypdf`-Nachbearbeitung, bleibt aber eine unveränderte, nur jetzt transparent kommunizierte Einschränkung.

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

- `uv run pytest services/rendering-service/tests` (**46 Tests**, vorher 44, +2 seit **Post-Roadmap Phase
  20 Session 7** ([ADR 0083](../adr/0083-admin-ui-processing-failures-visibility.md)): `document_id`
  optional in `GET /renditions` — ein Repository- und ein API-Test bestätigen den dokumentübergreifenden
  Aufruf mit `status`-Filter, ohne dass ein `422` zurückkommt; davor 44, +11 seit **Post-Roadmap Phase 20 Session 4** — Backoff-Verhalten, `list_due_for_retry`-Filterung, `reset_for_retry`-Regressionstest, `process_version`s `failed_permanent`-Pfad, neuer `/retry`-Endpunkt, neue `test_main.py` für `_run_retry_tick`, siehe [ADR 0080](../adr/0080-rendering-ocr-service-retry-backoff-failed-permanent.md)): Renderer-Verhalten gegen echte, in-memory erzeugte Dateien (echtes PNG/`.docx`/`.pptx`/PDF, keine Fixture-Dateien, keine Mocks), Repository (Upsert/Überschreiben/Filter), Pipeline (`process_version` direkt gegen den echten laufenden Document/Storage Service, inkl. Fehler-Isolation bei korruptem PDF; seit P5-S3 zusätzlich `process_ocr_text` für den Nachzieheffekt), API (`/renditions`-Endpunkte, Wasserzeichen inkl. Ablehnung bei ungültigem PDF), Consumer-Integration (echtes NATS-Event `document.created`/`document.version.created` löst echtes Rendering aus; seit P5-S3 zusätzlich `test_ocr_consumer.py` für den `ocr.completed`-Dispatch inkl. Duplikatsprüfung, mit einem Fake-`OcrServiceClient` statt eines echten OCR-Service-Aufrufs; seit P5b-S5 zusätzlich ein Regressionstest mit einem `OcrServiceClient`, der einen Verbindungsfehler simuliert — der Handler darf dabei nicht crashen).
- Live gegen den echten Stack über das API-Gateway verifiziert: Upload → automatische Thumbnail-Erzeugung → Download der Ersatzdarstellung (korrekt herunterskaliertes echtes PNG) → `rendering.completed` im Audit-Trail sichtbar, Hash-Kette weiterhin intakt. Seit P5-S3 zusätzlich: PDF mit Textlayer hochgeladen → OCR Service erzeugt `native_text_layer`-Ergebnis → rendering-service erzeugt automatisch eine `substitute_text`-Rendition mit exakt demselben Volltext. Seit P7-S3 zusätzlich: `.txt`- und echte `.docx`-Datei (via `python-docx` erzeugt) live über `soffice --headless` zu validem PDF/A-getaggtem PDF konvertiert.
- Seit P7-S3 zusätzlich `_libreoffice.py`-Tests (Binärpfad-Auflösung inkl. Fehlerfall bei fehlendem Binary via `monkeypatch` auf `_BINARY_CANDIDATES`) sowie erweiterte `PdfArchiveRenderer`-Tests für Bild-/Office-Formate.

## Offene Punkte

- **Bildbasierte/gescannte Dokumente**: seit P5-S3 über den Nachzieheffekt (Text) bedient — eine echte Bild-Ersatzdarstellung (Thumbnail) für gescannte/bildbasierte PDFs entsteht weiterhin nicht in rendering-service selbst, sondern nur als OCR-eigenes Seitenbild (siehe `docs/services/ocr-service.md`).
- **PDF/A weiterhin nicht ISO-19005-validiert** (s. o., seit P7-S3 zumindest über LibreOffices eigenen Export-Filter statt reinem `pypdf`-Tagging) — eine unabhängige veraPDF-Prüfung ist nicht Teil dieses Service.
- **Größeres Docker-Image** durch die LibreOffice-Installation (seit P7-S3) — bewusster Trade-off für die geforderte Formatabdeckung (5.6), kein Weg daran vorbei ohne proprietäre Cloud-APIs.
- **Kein Video-Transkriptions-Plugin**: laut Konzept selbst optional, keine Engine vorhanden.
- ~~**Keine Bereinigung fehlgeschlagener Renditions**~~ — **behoben in Post-Roadmap Phase 20 Session 4** ([ADR 0080](../adr/0080-rendering-ocr-service-retry-backoff-failed-permanent.md)): automatischer Retry mit Full-Jitter-Backoff bis `max_rendering_attempts`, danach `failed_permanent` + manueller Neustart über `POST .../retry` (gezielt nur für den betroffenen Renderer).
- ~~Keine Autorisierung~~ — **behoben in Post-Roadmap Phase 19 Session 8** ([ADR 0073](../adr/0073-ocr-rendering-virus-scan-rbac.md)): alle Endpunkte prüfen jetzt `rendering.read`/`rendering.write` über `permission-service`. Weiterhin offen: Ersatzdarstellungen erben laut 2.4 dieselben Berechtigungen wie das Original (feingranular, dokumentspezifisch) — die neue Prüfung ist ein grobes, service-weites `read`/`write`, keine Vererbung der konkreten Dokument-Berechtigung.
- **Wasserzeichen-Endpunkt bewusst minimal**: fester diagonaler Stempel, keine Positions-/Wiederholungs-/Farbkonfiguration.
