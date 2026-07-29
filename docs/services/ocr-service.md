# ocr-service

**Verantwortung:** Texterkennung inkl. Wort-Bounding-Boxen für bild-/PDF-basierte Dokumente (Konzept 3.9) — erkennt automatisch, ob ein Dokument überhaupt OCR braucht (vorhandener nutzbarer Textlayer ja/nein), speist den künftigen Search Service (P5-S4) sowie einen Nachzieheffekt im Rendering Service, und liefert die Wort-Positionen für die positionsgenaue Text-Markierung in der Vorschau (Nutzer-Feedback nach P5-S2).

**Konzept-Referenz:** 3.9
**Eigenes Postgres-Schema:** `ocr` (`ocr_result`, `ocr_config`).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/ocr-results?document_id=...&version_number=...` | OCR-Ergebnisse zu einer Version (`version_number` optional — ohne Angabe: alle Versionen des Dokuments) |
| `GET` | `/ocr-results/{id}` | Einzelnes OCR-Ergebnis (Volltext, Wort-Bounding-Boxen, Konfidenz) — 404 bei unbekannter `id` |
| `GET` | `/ocr-results/{id}/page-image` | Vom OCR Service selbst gerastertes Seitenbild (nur PDFs, siehe unten) — 404 bei unbekannter `id`, 409 wenn kein eigenständiges Seitenbild existiert (Rasterbild-Fall) |
| `GET` | `/config` | Aktuelle Konfiguration (`max_word_count`, `batch_size`, `allowed_content_types`, `updated_at`) — legt beim allerersten Aufruf die Default-Zeile an (P5b-S5) |
| `PUT` | `/config` | Aktualisiert `max_word_count`/`batch_size`/`allowed_content_types`, wirkt ohne Neustart auf das nächste verarbeitete Dokument (Admin-UI "OCR-Einstellungen") |
| `GET` | `/healthz` | Health-Check |

`id` eines OCR-Ergebnisses ist ein natürlicher Schlüssel `{document_id}:{version_number}` (siehe Datenmodell) — anders als bei rendering-service gibt es hier bewusst nur ein autoritatives Ergebnis je Version, kein Diskriminator für mehrere Regeln.

## Automatische OCR-Pipeline (3.9)

Konsumiert `document.created`/`document.version.created` vom Document Service — identisches Muster wie rendering-service (ein breites `document.>`-Abo, In-Handler-Dispatch nach `event_type`, Backfill beim ersten Start). Für jede neue Version:

1. Metadaten/Originalinhalt werden über die HTTP-API des Document Service bezogen (kein direkter Zugriff auf dessen Schema/Storage-Key, 3.1).
2. **Automatische Erkennung, ob OCR überhaupt nötig ist** (3.9): Bei PDFs wird zuerst versucht, den vorhandenen Textlayer direkt auszulesen (PyMuPDF `get_text("words")`) — liefert Seite 1 mindestens 20 nicht-leere Zeichen, gilt der Textlayer als nutzbar, es findet **keine** Bilderkennung statt (`NativeTextLayerEngine`, Konfidenz immer 100.0, exakt statt geschätzt). Sonst (gescanntes PDF ohne Textlayer, oder ein Rasterbild direkt) läuft `TesseractEngine`.
3. Andere Formate (`.docx`, `.pptx`, Video, ...) werden von keiner Engine unterstützt — es entsteht kein OCR-Ergebnis, kein Event (deren Textextraktion übernimmt bereits `DocxTextExtractionRenderer`/`PptxTextExtractionRenderer` aus P5-S2).
4. Für PDFs rastert der jeweilige Engine-Pfad zusätzlich ein eigenständiges Seitenbild (Seite 1, `raster_dpi=150`) und legt es über den Storage Service ab — rendering-service erzeugt **keine** PDF-Thumbnails (`ThumbnailRenderer.supports()` prüft nur `image/*`), dieses Seitenbild ist aktuell die einzige Bildvorschau für PDFs im System. Für Rasterbilder entsteht kein eigenständiges Seitenbild — die Vorschau nutzt die bereits vorhandene `thumbnail`-Rendition aus rendering-service, OCR liefert dafür die nativen Pixelmaße des Originalbilds als Referenzgröße für die Wort-Koordinaten.
5. Ergebnis wird dauerhaft unter dem natürlichen Schlüssel gespeichert (Upsert, idempotent bei Redelivery) und als `ocr.completed`/`ocr.failed` veröffentlicht.

**Nur Seite 1**: konsistent mit rendering-service's eigenem Ein-Bild-Thumbnail-Scope — Mehrseiten-OCR ist nicht Teil dieser Session.

## Konfigurierbarkeit (3.9, P5b-S5, [ADR 0016](../adr/0016-ocr-configurability-compose-profile-and-live-settings.md))

Drei in 3.9 geforderte Stellschrauben, bewusst über zwei unterschiedliche Mechanismen umgesetzt (Begründung siehe ADR):

- **`ocrEnabled`** — Docker-Compose-Profil-Opt-out, **kein** Feld in `/config`: `ocr-service` trägt `profiles: ["ocr"]`; ist das Profil (`COMPOSE_PROFILES`, Default `ocr`) nicht aktiv, wird der Container gar nicht erst deployt. `rendering-service`/`search-service` hängen deshalb bewusst nicht per `depends_on` an `ocr-service` und tolerieren dessen Abwesenheit über ihre HTTP-Clients (`get_full_text()`).
- **Maximale Wortobergrenze** (`max_word_count`, `null` = keine Grenze) — vor dem eigentlichen Engine-Aufruf wird die Wortzahl **geschätzt** (`engines.estimate_word_count()`: PDF-Seitenzahl × 250, Rasterbilder zählen immer als eine Seite) und mit der konfigurierten Grenze verglichen. Überschritten → `status="skipped"`, Event `ocr.skipped`, **keine** Engine läuft (das ist der eigentliche Zweck: teure Tesseract-Läufe auf sehr große Scans vermeiden).
- **Verarbeitungs-Batch-Size** (`batch_size`, Default `4`) — Nebenläufigkeitsgrenze für gleichzeitig laufende `process_version()`-Aufrufe. `NatsEventBusClient.subscribe()` (`dms-eventbus-client`) bekam dafür einen neuen optionalen `max_concurrency`-Parameter (Default `1`, für alle anderen Konsumenten unverändert); `ocr-service` übergibt ein Callable, das `OcrConfig.batch_size` bei jeder Nachricht live aus der DB liest.
- **Content-Type-Positivliste** (`allowed_content_types`, leer = keine Einschränkung, seit P5d-S1) — greift **zusätzlich** zur technischen `select_engine()`-Auswahl: ist die Liste nicht leer, läuft OCR nur noch für die dort genannten Content-Types, z. B. "nur `application/pdf`, nicht `image/tiff`", obwohl Letzteres technisch möglich wäre (Nutzer-Feedback: nicht jeder OCR-fähige Dateityp soll auch tatsächlich OCR auslösen). Prüft `metadata.content_type` (vom Document Service per Sniffing ermittelt, siehe `docs/services/document-service.md`) gegen die Liste, bevor die Wortobergrenze geprüft wird. Kein Treffer → `status="skipped"`, Event `ocr.skipped` mit `content_type` statt `estimated_words` im Payload.

Alle drei DB-gestützten Werte (`max_word_count`/`batch_size`/`allowed_content_types`) liegen in der einzeiligen `OcrConfig`-Tabelle (feste `id=1`, per `GET`/`PUT /config` administrierbar) und wirken **ohne Neustart** — anders als jede bisherige `Settings`-Umgebungsvariable in diesem Repo.

## Engine-Plugins (3.3/3.8, gleiches Prinzip wie Storage-Backends/Renderer)

| Engine | Wann | Konfidenz | Implementierung |
|---|---|---|---|
| `NativeTextLayerEngine` (`native_text_layer`) | PDF mit nutzbarem Textlayer | immer `100.0` (exakt, nicht OCR'd) | PyMuPDF `get_text("words")`, skaliert von PDF-Punkten in Pixel des gleichzeitig gerasterten Seitenbilds |
| `TesseractEngine` (`tesseract`) | Gescanntes PDF ohne Textlayer, oder Rasterbild direkt | Mittelwert der Tesseract-Wort-Konfidenzen (`0`–`100`) | `pytesseract.image_to_data(..., lang="deu+eng")` |
| PaddleOCR | — | — | **nicht implementiert**, siehe [ADR 0011](../adr/0011-ocr-tesseract-over-paddleocr.md) |

`select_engine()` (`engines/__init__.py`) wählt anhand der Textlayer-Erkennung **genau eine** Engine (nicht wie `select_renderers()` eine Liste unabhängiger Regeln) — OCR erzeugt ein autoritatives Ergebnis je Version.

**Bewusste Abweichung vom Konzept-Beispieltext, dieselbe Abwägung wie ClamdEngine vs. EicarSignatureEngine (ADR 0010)**: Das Konzept nennt PaddleOCR als Standard-Engine — `paddlepaddle` ist als vollständiges ML-Framework (mehrere hundert MB) in dieser Umgebung nicht praktikabel. Tatsächlich verdrahtete Standard-Engine ist **Tesseract** (`apt-get install tesseract-ocr tesseract-ocr-deu` im Dockerfile, `pytesseract` als Python-Wrapper) — anders als bei ClamdEngine (ein dünner Protokoll-Client, der vollständig implementiert wurde) gibt es für PaddleOCR keine leichtgewichtige Teilimplementierung, die das schwere Framework vermeidet; es ist daher nur dokumentiert, nicht gebaut. Details siehe ADR 0011.

## `needs_review` statt echter BPMN-Anbindung (3.9)

3.9 sieht bei niedrigem Konfidenzwert optional eine manuelle Nachprüfung als BPMN-Prozessschritt vor — die Workflow Engine existiert aber erst ab P6-S1. Diese Session baut daher nur einen einfachen Zwischenzustand: `average_confidence < 70.0` → `status="needs_review"`, veröffentlicht als Event, **ohne** die Verfügbarkeit des Dokuments zu blockieren (anders als beim Virenscan, ADR 0010 — Rendering/OCR sind immer nicht-blockierende Nebeneffekte). Die echte BPMN-Anbindung folgt vermutlich zusammen mit dem für P6-S4 vorgesehenen generischen Approval-Mechanismus.

## Nachzieheffekt in rendering-service (2.4/3.9)

rendering-service abonniert zusätzlich `ocr.completed` (eigener Durable-Name `rendering-service-ocr`, getrennt vom `document.>`-Abo) und erzeugt daraus eine `substitute_text`-Rendition aus dem OCR-Volltext, sofern noch keine existiert — schließt die in P5-S2 bewusst offen gelassene Lücke für gescannte/bildbasierte Dokumente (und, als Nebeneffekt, auch für PDFs mit echtem Textlayer, für die es bislang nur die `pdf_archive`-Kopie gab, keine Textextraktion). `ocr.completed`-Events tragen bewusst nur Statusfelder, nicht den potenziell großen Volltext selbst — rendering-service holt ihn per HTTP nach (`GET /ocr-results/{id}`), um NATS-Payloads und die Audit-Hashkette klein zu halten. Details siehe `docs/services/rendering-service.md`.

## Anbindung an das Backend

- **Document Service** (3.1): `GET /documents/{id}/versions/{n}` (Metadaten) und `.../content` (Originalbytes) — kein direkter Zugriff auf dessen Schema/Storage-Key.
- **Storage Service** (3.6): `PUT`/`GET /objects/ocr/{document_id}/{version_number}/page-1.png` — Persistenz der eigenständigen PDF-Seitenbilder.

## Events

| Event | Payload | Wann |
|---|---|---|
| `ocr.completed` | `{version_number, status: "ready"\|"needs_review", engine, average_confidence}` | Nach erfolgreicher Extraktion |
| `ocr.failed` | `{version_number, error}` | Bei nicht lesbarem PDF oder einer Engine-Exception |
| `ocr.skipped` | `{version_number, estimated_words}` | Geschätzte Wortzahl übersteigt die konfigurierte Obergrenze (P5b-S5) — keine Engine lief |
| `ocr.skipped` | `{version_number, content_type}` | Content-Type steht nicht auf der Positivliste (P5d-S1) — keine Engine lief |

Der Audit Service konsumiert `ocr.>` seit dieser Session (siehe `docs/services/audit-service.md`).

## Selbst-Registrierung (Konzept 3.2a)

Meldet sich beim Start über `dms-registry-client` selbst bei der Registry an — Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

- `uv run pytest services/ocr-service/tests`: Engines (`NativeTextLayerEngine`/`TesseractEngine` gegen echte, in-memory erzeugte PDFs/Bilder, `select_engine()`-Dispatch für alle Fälle inkl. korruptem PDF, `estimate_word_count()` für Mehrseiten-PDF/Rasterbild/kaputtes PDF), Repository (Upsert/Überschreiben/Filter, `OcrConfig` Default-Anlage/Update/Zurücksetzen), Pipeline (`process_version` direkt gegen den echten laufenden Document/Storage Service, inkl. `DocumentNotFoundError`-Pfad ohne NATS-Redelivery-Risiko, sowie der `skipped`-Pfad bei niedrig konfigurierter Wortobergrenze bzw. bei per `allowed_content_types`-Positivliste nicht abgedecktem Content-Type inkl. Gegenprobe, dass ein gelisteter Content-Type ganz normal verarbeitet wird, P5d-S1), API (inkl. `GET`/`PUT /config`, Validierungsfehler bei `batch_size` außerhalb `1..64`, Persistenz von `allowed_content_types`), Consumer-Integration (echtes NATS-Event löst echte OCR aus). Die beiden `TesseractEngine`-Tests sind mit `pytest.mark.skipif(shutil.which("tesseract") is None, ...)` versehen, da diese Entwicklungsumgebung selbst keinen `tesseract`-Systembinary hat (nur der Docker-Container, siehe Dockerfile) — Verifikation erfolgt dort per Live-E2E. Der neue `max_concurrency`-Parameter von `NatsEventBusClient.subscribe()` wird eigenständig in `libs/dms-eventbus-client/tests/test_nats_backend.py` getestet (Default bleibt streng sequentiell, `max_concurrency>1` lässt Handler nachweislich parallel laufen, ein fehlschlagender Handler blockiert die freigewordene Kapazität nicht).
- **Live-E2E über den echten Gateway-Stack** (Session-Abschluss): echtes PDF mit Textlayer hochgeladen → `native_text_layer`-Ergebnis mit korrekten Wort-Bounding-Boxen und passendem Seitenbild (834×625 PNG), `average_confidence=100.0` → rendering-service erzeugt automatisch eine `substitute_text`-Rendition mit exakt demselben Text → Audit-Trail zeigt sowohl `ocr.completed` als auch `rendering.completed`, Hash-Kette bleibt intakt. Gateway-Routing erzwingt Auth (401 ohne/mit ungültigem Token) wie bei allen anderen Services. P5b-S5 ergänzt: `PUT /config` mit niedriger Wortobergrenze → Upload eines Dokuments → `status="skipped"` sichtbar über `GET /ocr-results`.

## Offene Punkte

- **PaddleOCR nicht implementiert**: nur die Plugin-Schnittstelle lässt es zu, siehe ADR 0011.
- **Nur Seite 1**: Mehrseiten-OCR/-Vorschau ist nicht Teil dieser Session.
- **`needs_review` ohne echte Workflow-Anbindung**: BPMN-gestützte manuelle Nachprüfung folgt frühestens mit P6-S1/P6-S4.
- **Keine automatische Nachverarbeitung bei dauerhaftem `failed`**: kein Retry-Mechanismus, analog zu rendering-service.
- **Keine Autorisierung** (wie bei allen bisherigen Services): Gateway prüft nur Token-Gültigkeit, keine Rollenprüfung.
- **Wortobergrenze ist eine grobe Schätzung** (P5b-S5): `Seitenzahl × 250` statt exakter Zählung — kann bei textarmen mehrseitigen PDFs zu früh und bei textdichten Einzelbildern nie greifen (Details/Begründung siehe ADR 0016).
- **Batch-Size begrenzt nur die Anzahl gleichzeitiger Aufrufe, nicht den Ressourcenverbrauch je Aufruf** — kein echter Worker-Pool mit Speicher-/CPU-Accounting.
- **`ocrEnabled` ist nur als Compose-Profil sichtbar/steuerbar** — die Admin-UI zeigt lediglich "erreichbar"/"nicht erreichbar", kein Schalter (Begründung siehe ADR 0016).
