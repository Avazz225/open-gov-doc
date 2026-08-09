# ocr-service

**Verantwortung:** Texterkennung inkl. Wort-Bounding-Boxen für bild-/PDF-basierte Dokumente (Konzept 3.9) — erkennt automatisch, ob ein Dokument überhaupt OCR braucht (vorhandener nutzbarer Textlayer ja/nein), speist den künftigen Search Service (P5-S4) sowie einen Nachzieheffekt im Rendering Service, und liefert die Wort-Positionen für die positionsgenaue Text-Markierung in der Vorschau (Nutzer-Feedback nach P5-S2).

**Konzept-Referenz:** 3.9
**Eigenes Postgres-Schema:** `ocr` (`ocr_result`, `ocr_config`).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/ocr-results?document_id=...&version_number=...` | OCR-Ergebnisse zu einer Version (`version_number` optional — ohne Angabe: alle Versionen des Dokuments) |
| `GET` | `/ocr-results/{id}` | Einzelnes OCR-Ergebnis (Volltext, Wort-Bounding-Boxen, Konfidenz) — 404 bei unbekannter `id` |
| `GET` | `/ocr-results/{id}/page-image?page_number=...` | Vom OCR Service selbst gerastertes Seitenbild einer Seite (nur PDFs, siehe unten; `page_number` optional, Default `1`) — 404 bei unbekannter `id` oder außerhalb des gültigen Seitenbereichs, 409 wenn kein eigenständiges Seitenbild existiert (Rasterbild-Fall) |
| `GET` | `/config` | Aktuelle Konfiguration (`max_word_count`, `batch_size`, `allowed_content_types`, `updated_at`) — legt beim allerersten Aufruf die Default-Zeile an (P5b-S5) |
| `PUT` | `/config` | Aktualisiert `max_word_count`/`batch_size`/`allowed_content_types`, wirkt ohne Neustart auf das nächste verarbeitete Dokument (Admin-UI "OCR-Einstellungen") |
| `GET` | `/healthz` | Health-Check |

`id` eines OCR-Ergebnisses ist ein natürlicher Schlüssel `{document_id}:{version_number}` (siehe Datenmodell) — anders als bei rendering-service gibt es hier bewusst nur ein autoritatives Ergebnis je Version, kein Diskriminator für mehrere Regeln.

## Automatische OCR-Pipeline (3.9)

Konsumiert `document.created`/`document.version.created` vom Document Service — identisches Muster wie rendering-service (ein breites `document.>`-Abo, In-Handler-Dispatch nach `event_type`, Backfill beim ersten Start). Für jede neue Version:

1. Metadaten/Originalinhalt werden über die HTTP-API des Document Service bezogen (kein direkter Zugriff auf dessen Schema/Storage-Key, 3.1).
2. **Automatische Erkennung, ob OCR überhaupt nötig ist** (3.9): Bei PDFs wird zuerst versucht, den vorhandenen Textlayer direkt auszulesen (PyMuPDF `get_text("words")`) — liefert Seite 1 mindestens 20 nicht-leere Zeichen, gilt der Textlayer als nutzbar, es findet **keine** Bilderkennung statt (`NativeTextLayerEngine`, Konfidenz immer 100.0, exakt statt geschätzt). Sonst (gescanntes PDF ohne Textlayer, oder ein Rasterbild direkt) läuft `TesseractEngine`.
3. Andere Formate (`.docx`, `.pptx`, Video, ...) werden von keiner Engine unterstützt — es entsteht kein OCR-Ergebnis, kein Event (deren Textextraktion übernimmt bereits `DocxTextExtractionRenderer`/`PptxTextExtractionRenderer` aus P5-S2).
4. Für PDFs rastert der jeweilige Engine-Pfad zusätzlich **ein eigenständiges Seitenbild je Seite** (`raster_dpi=150`) und legt sie über den Storage Service ab — rendering-service erzeugt **keine** PDF-Thumbnails (`ThumbnailRenderer.supports()` prüft nur `image/*`), diese Seitenbilder sind aktuell die einzige Bildvorschau für PDFs im System. Für Rasterbilder entsteht kein eigenständiges Seitenbild — die Vorschau nutzt die bereits vorhandene `thumbnail`-Rendition aus rendering-service, OCR liefert dafür die nativen Pixelmaße des Originalbilds als Referenzgröße für die Wort-Koordinaten.
5. Ergebnis wird dauerhaft unter dem natürlichen Schlüssel gespeichert (Upsert, idempotent bei Redelivery) und als `ocr.completed`/`ocr.failed` veröffentlicht.

**Alle Seiten, nicht nur die erste** (Bugfix nach Nutzer-Feedback, ursprünglich nur Seite 1 unterstützt): `NativeTextLayerEngine`/`TesseractEngine` durchlaufen alle Seiten des Dokuments (`OcrExtractionResult.page_images: list[bytes]`, 1:1 zu `pages`), die Pipeline legt ein Bild je Seite ab. Der Textlayer-Verfügbarkeitscheck (`_native_text_available()`) prüft weiterhin nur Seite 1 als Heuristik für die Engine-Auswahl — ein Dokument gilt damit ganz oder gar nicht als "hat nutzbaren Textlayer", eine Seiten-gemischte Erkennung (z. B. Seite 1 nativ, Seite 2 gescannt) ist nicht vorgesehen.

## Konfigurierbarkeit (3.9, P5b-S5, [ADR 0016](../adr/0016-ocr-configurability-compose-profile-and-live-settings.md))

Drei in 3.9 geforderte Stellschrauben, bewusst über zwei unterschiedliche Mechanismen umgesetzt (Begründung siehe ADR):

- **`ocrEnabled`** — Docker-Compose-Profil-Opt-out, **kein** Feld in `/config`: `ocr-service` trägt `profiles: ["ocr"]`; ist das Profil (`COMPOSE_PROFILES`, Default `ocr`) nicht aktiv, wird der Container gar nicht erst deployt. `rendering-service`/`search-service` hängen deshalb bewusst nicht per `depends_on` an `ocr-service` und tolerieren dessen Abwesenheit über ihre HTTP-Clients (`get_full_text()`).
- **Maximale Wortobergrenze** (`max_word_count`, `null` = keine Grenze) — vor dem eigentlichen Engine-Aufruf wird die Wortzahl **geschätzt** (`engines.estimate_word_count()`: PDF-Seitenzahl × 250, Rasterbilder zählen immer als eine Seite) und mit der konfigurierten Grenze verglichen. Überschritten → `status="skipped"`, Event `ocr.skipped`, **keine** Engine läuft (das ist der eigentliche Zweck: teure Tesseract-Läufe auf sehr große Scans vermeiden).
- **Verarbeitungs-Batch-Size** (`batch_size`, Default `4`) — Nebenläufigkeitsgrenze für gleichzeitig laufende `process_version()`-Aufrufe. `NatsEventBusClient.subscribe()` (`dms-eventbus-client`) bekam dafür einen neuen optionalen `max_concurrency`-Parameter (Default `1`, für alle anderen Konsumenten unverändert); `ocr-service` übergibt ein Callable, das `OcrConfig.batch_size` bei jeder Nachricht live aus der DB liest.
- **Content-Type-Positivliste** (`allowed_content_types`, leer = keine Einschränkung, seit P5d-S1; **Default seit einem weiteren Nutzer-Feedback-Durchgang `["application/pdf"]`** statt leer — OCR soll standardmäßig nur für PDFs laufen, alles andere (insbesondere Bilder) erfordert eine bewusste Admin-Freigabe über `PUT /config`) — greift **zusätzlich** zur technischen `select_engine()`-Auswahl: ist die Liste nicht leer, läuft OCR nur noch für die dort genannten Content-Types, z. B. "nur `application/pdf`, nicht `image/tiff`", obwohl Letzteres technisch möglich wäre. Prüft `metadata.content_type` (vom Document Service per Sniffing ermittelt, siehe `docs/services/document-service.md`) gegen die Liste, bevor die Wortobergrenze geprüft wird. Kein Treffer → `status="skipped"`, Event `ocr.skipped` mit `content_type` statt `estimated_words` im Payload. Der neue Default gilt nur für **frisch angelegte** Konfigurationszeilen (`get_config()`s Erstanlage) — bereits bestehende Installationen mit einer zuvor gespeicherten `[]`-Zeile behalten diese unverändert, bis ein Admin sie explizit über `PUT /config` ändert.

Alle drei DB-gestützten Werte (`max_word_count`/`batch_size`/`allowed_content_types`) liegen in der einzeiligen `OcrConfig`-Tabelle (feste `id=1`, per `GET`/`PUT /config` administrierbar) und wirken **ohne Neustart** — anders als jede bisherige `Settings`-Umgebungsvariable in diesem Repo.

## Engine-Plugins (3.3/3.8, gleiches Prinzip wie Storage-Backends/Renderer)

| Engine | Wann | Konfidenz | Implementierung |
|---|---|---|---|
| `NativeTextLayerEngine` (`native_text_layer`) | PDF mit nutzbarem Textlayer | immer `100.0` (exakt, nicht OCR'd) | PyMuPDF `get_text("words")`, skaliert von PDF-Punkten in Pixel des gleichzeitig gerasterten Seitenbilds |
| `TesseractEngine` (`tesseract`) | Gescanntes PDF ohne Textlayer, oder Rasterbild direkt | Mittelwert der Tesseract-Wort-Konfidenzen (`0`–`100`) | `pytesseract.image_to_data(..., lang="deu+eng")` |
| PaddleOCR | — | — | **nicht implementiert**, siehe [ADR 0011](../adr/0011-ocr-tesseract-over-paddleocr.md) |

`select_engine()` (`engines/__init__.py`) wählt anhand der Textlayer-Erkennung **genau eine** Engine (nicht wie `select_renderers()` eine Liste unabhängiger Regeln) — OCR erzeugt ein autoritatives Ergebnis je Version.

**Bewusste Abweichung vom Konzept-Beispieltext, dieselbe Abwägung wie ClamdEngine vs. EicarSignatureEngine (ADR 0010)**: Das Konzept nennt PaddleOCR als Standard-Engine — `paddlepaddle` ist als vollständiges ML-Framework (mehrere hundert MB) in dieser Umgebung nicht praktikabel. Tatsächlich verdrahtete Standard-Engine ist **Tesseract** (`apt-get install tesseract-ocr tesseract-ocr-deu` im Dockerfile, `pytesseract` als Python-Wrapper) — anders als bei ClamdEngine (ein dünner Protokoll-Client, der vollständig implementiert wurde) gibt es für PaddleOCR keine leichtgewichtige Teilimplementierung, die das schwere Framework vermeidet; es ist daher nur dokumentiert, nicht gebaut. Details siehe ADR 0011.

## Durchsuchbare PDF statt reinem Scan (Nutzer-Feedback)

Nutzer-Feedback (Vermutung, die sich bei der Recherche als falsch herausstellte — OCR erzeugte
noch nie eine Dokumentversion, nur eigene `OcrResult`-Zeilen): der eigentliche Wunsch dahinter
war ein echtes, bis dahin fehlendes Feature — ein Scan ohne nutzbaren Textlayer soll eine neue,
**durchsuchbare** PDF-Version bekommen (Original + Original-Bytes bleiben als Version davor
unverändert erhalten), statt nur Wort-Koordinaten für das bestehende Overlay zu liefern.

- **Mechanismus** (`text_layer.py`, `embed_text_layer()`): fügt Tesseracts erkannte Wörter als
  **unsichtbaren Textlayer** (`page.insert_text(..., render_mode=3)`, PDF-Standard-Textrendermodus
  3 — exakt das Prinzip, mit dem auch `ocrmypdf` seinen OCR-Textlayer einbettet) an den korrekten
  Positionen in eine Kopie der Original-PDF-Bytes ein. Tesseracts Wortboxen liegen in
  Pixel-Koordinaten bei `raster_dpi` (Default 150) — Umrechnung in PDF-Punkte über
  `scale = 72 / raster_dpi`, derselbe Koordinatenursprung (oben links, y wächst nach unten) in
  Pixmap und PDF-Seite, keine Achsenspiegelung nötig. Das sichtbare Erscheinungsbild der Seite
  bleibt unverändert — nur ihre Durchsuchbarkeit ändert sich.
- **Neue Version statt neuer Rendition** (Rückfrage bei Sessionstart, Nutzer wählte die
  empfohlene Option): `document_client.create_version()` checkt die veränderten Bytes über
  `POST /documents/{id}/versions` ein — 1:1 dasselbe Muster wie
  `signature_service.document_client.checkin_signed_version()` (ADR 0025: Verarbeitung, die PDF-
  Bytes tatsächlich verändert, erzeugt serverseitig eine neue Version statt die Originalversion
  zu überschreiben). `created_by="system:ocr-service"`, `comment="OCR: durchsuchbarer Textlayer
  eingebettet"`.
- **Nur für PDFs, nicht für Bilder** (Rückfrage bei Sessionstart): für Rasterbilder gibt es kein
  äquivalentes "unsichtbarer Textlayer im Bildformat"-Konzept. Bilder behalten ihr Format
  unverändert; ihr erkannter Text bleibt stattdessen über `OcrResult.full_text` abrufbar, den
  `PreviewPane.tsx` seit demselben Durchgang zusätzlich als eigenen, klar getrennten Textextrakt
  neben dem Bild anzeigt (siehe `docs/services/user-ui.md`).
- **Zwei reale, beim Testen gefundene Bugs, beide vor dem Live-Rollout behoben:**
  1. Eine Seite ganz ohne erkannte Wörter (`extraction.pages` ohne ein einziges Wort, z. B. eine
     wirklich leere Testseite) bekam trotzdem eine neue, sinnlose Version mit leerem Textlayer —
     entdeckt, weil eine genau solche Test-Fixture-PDF in `signature-service`s eigener Testsuite
     dessen Versionsnummern-Annahmen durcheinanderbrachte (der `ocr-service`-Container läuft
     während der Testläufe **anderer** Services live weiter und verarbeitet jedes hochgeladene
     PDF, auch aus fremden Testsuiten). Fix: die Einbettung läuft nur noch, wenn mindestens ein
     Wort erkannt wurde (`has_recognized_words`).
  2. **Fehlende Terminierung des Reprocessing-Kreislaufs bei kurzem erkanntem Text**: die neu
     erzeugte Version löst selbst wieder `document.version.created` aus und wird erneut
     verarbeitet. Die ursprüngliche Annahme war, dass `_native_text_available()`s
     Zeichen-Schwelle (`min_native_text_chars=20`) das nach einem Durchlauf zuverlässig beendet,
     da die neue Version dann echten Text hat. Real beobachtet: ein einzelnes kurzes Wort (unter
     20 Zeichen) reichte dafür nicht — die unsichtbar eingebetteten Wörter verändern die von
     Tesseract beim erneuten Rastern gesehenen Pixel nicht, `TesseractEngine` lief also ein
     zweites Mal, erkannte dasselbe sichtbare Wort erneut und bettete eine **zweite** Kopie
     davon ein, bis die Schwelle erst dadurch überschritten wurde — bei noch kürzerem Text hätte
     sich das unbegrenzt fortgesetzt. Fix: `VersionMetadata` trägt jetzt zusätzlich
     `created_by`; die Einbettung läuft nicht, wenn die aktuell verarbeitete Version bereits von
     `system:ocr-service` selbst stammt (`OCR_SERVICE_ACTOR`-Sentinel) — eine explizite,
     inhaltsunabhängige Sperre statt sich auf einen inzidentellen Zeichen-Schwellenwert zu
     verlassen.
- **Bekannte, akzeptierte Randfall-Einschränkung aus Bug 2**: bei sehr kurzem erkanntem Text
  (unter den 20 Zeichen von `min_native_text_chars`) bleibt die per Textlayer-Einbettung erzeugte
  neue Version bei einem erneuten `OcrResult` mit `engine="tesseract"` stehen (nicht
  `"native_text_layer"`), obwohl sie technisch bereits einen echten, durchsuchbaren Textlayer
  hat — `PreviewPane.tsx`s Engine-Weiche würde für diesen schmalen Fall weiterhin das
  Seitenbild-Overlay statt der nativen Ansicht zeigen. Für reale, mehrzeilige Scans (weit über
  20 Zeichen) tritt dieser Fall nicht auf; als bewusste, dokumentierte Grenze akzeptiert statt
  mit zusätzlicher Komplexität (z. B. einer eigenen "wurde bereits eingebettet"-Markierung
  unabhängig vom Zeichen-Schwellenwert) beseitigt.

## `needs_review` statt echter BPMN-Anbindung (3.9)

3.9 sieht bei niedrigem Konfidenzwert optional eine manuelle Nachprüfung als BPMN-Prozessschritt vor — die Workflow Engine existiert aber erst ab P6-S1. Diese Session baut daher nur einen einfachen Zwischenzustand: `average_confidence < 70.0` → `status="needs_review"`, veröffentlicht als Event, **ohne** die Verfügbarkeit des Dokuments zu blockieren (anders als beim Virenscan, ADR 0010 — Rendering/OCR sind immer nicht-blockierende Nebeneffekte). Die echte BPMN-Anbindung folgt vermutlich zusammen mit dem für P6-S4 vorgesehenen generischen Approval-Mechanismus.

## Nachzieheffekt in rendering-service (2.4/3.9)

rendering-service abonniert zusätzlich `ocr.completed` (eigener Durable-Name `rendering-service-ocr`, getrennt vom `document.>`-Abo) und erzeugt daraus eine `substitute_text`-Rendition aus dem OCR-Volltext, sofern noch keine existiert — schließt die in P5-S2 bewusst offen gelassene Lücke für gescannte/bildbasierte Dokumente (und, als Nebeneffekt, auch für PDFs mit echtem Textlayer, für die es bislang nur die `pdf_archive`-Kopie gab, keine Textextraktion). `ocr.completed`-Events tragen bewusst nur Statusfelder, nicht den potenziell großen Volltext selbst — rendering-service holt ihn per HTTP nach (`GET /ocr-results/{id}`), um NATS-Payloads und die Audit-Hashkette klein zu halten. Details siehe `docs/services/rendering-service.md`.

## Anbindung an das Backend

- **Document Service** (3.1): `GET /documents/{id}/versions/{n}` (Metadaten) und `.../content` (Originalbytes) — kein direkter Zugriff auf dessen Schema/Storage-Key. Seit dem Textlayer-Feature (siehe oben) zusätzlich schreibend: `POST /documents/{id}/versions` zum Einchecken der textlayer-eingebetteten PDF als neue Version (`document_client.create_version()`, gleiches Muster wie `signature-service`, das bislang der einzige Aufrufer dieses Endpunkts außerhalb des Document Service selbst war).
- **Storage Service** (3.6): `PUT`/`GET /objects/ocr/{document_id}/{version_number}/page-{seitenzahl}.png` — Persistenz der eigenständigen PDF-Seitenbilder, ein Objekt je Seite (`OcrResult.page_image_storage_key` speichert dabei nur das Präfix ohne Seitensuffix).

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

- `uv run pytest services/ocr-service/tests` (**48 Tests**, vorher 40): Engines (`NativeTextLayerEngine`/`TesseractEngine` gegen echte, in-memory erzeugte PDFs/Bilder, `select_engine()`-Dispatch für alle Fälle inkl. korruptem PDF, `estimate_word_count()` für Mehrseiten-PDF/Rasterbild/kaputtes PDF), **neue `test_text_layer.py`** (4 Tests, keine Tesseract-/Document-Service-Abhängigkeit — reine `fitz`-Funktionstests: eingebetteter Text ist extrahierbar, Whitespace-Wörter werden übersprungen, ein Seitenindex jenseits des Dokuments bricht nicht ab, korrupte PDF-Bytes lösen `fitz.FileDataError` aus, wie es `pipeline.py`s Try/Except erwartet), Repository (Upsert/Überschreiben/Filter, `OcrConfig` Default-Anlage inkl. des neuen `["application/pdf"]`-Defaults/Update/Zurücksetzen), Pipeline (`process_version` direkt gegen den echten laufenden Document/Storage Service, inkl. `DocumentNotFoundError`-Pfad ohne NATS-Redelivery-Risiko, dem `skipped`-Pfad bei niedrig konfigurierter Wortobergrenze bzw. bei per `allowed_content_types`-Positivliste nicht abgedecktem Content-Type inkl. Gegenprobe, sowie **vier neue Tesseract-gegateten Tests**: ein Scan mit erkennbarem Text bekommt real eine neue, textlayer-eingebettete Version mit `created_by="system:ocr-service"`; eine leere Scan-PDF ohne erkennbare Wörter bekommt **keine** neue Version; die neu erzeugte Version selbst wird bei erneuter Verarbeitung **nicht** ein zweites Mal eingebettet (Regressionstest für den beim Testen gefundenen Endlos-Reprocessing-Bug, siehe "Durchsuchbare PDF" oben); ein Rasterbild bekommt trotz Tesseract-Lauf ebenfalls keine neue Version), API (inkl. `GET`/`PUT /config`, Validierungsfehler bei `batch_size` außerhalb `1..64`, Persistenz von `allowed_content_types`, neuer `["application/pdf"]`-Default), Consumer-Integration (echtes NATS-Event löst echte OCR aus). Alle fünf `TesseractEngine`-abhängigen Tests (der ursprüngliche Rasterbild-Test plus die vier neuen) sind mit `pytest.mark.skipif(shutil.which("tesseract") is None, ...)` versehen, da diese Entwicklungsumgebung selbst keinen `tesseract`-Systembinary hat (nur der Docker-Container, siehe Dockerfile) — Verifikation erfolgt dort per Live-E2E. Der `max_concurrency`-Parameter von `NatsEventBusClient.subscribe()` wird weiterhin eigenständig in `libs/dms-eventbus-client/tests/test_nats_backend.py` getestet.
- **Live-E2E über den echten Gateway-Stack** (Session-Abschluss): echtes PDF mit Textlayer hochgeladen → `native_text_layer`-Ergebnis mit korrekten Wort-Bounding-Boxen und passendem Seitenbild (834×625 PNG), `average_confidence=100.0` → rendering-service erzeugt automatisch eine `substitute_text`-Rendition mit exakt demselben Text → Audit-Trail zeigt sowohl `ocr.completed` als auch `rendering.completed`, Hash-Kette bleibt intakt. Gateway-Routing erzwingt Auth (401 ohne/mit ungültigem Token) wie bei allen anderen Services. P5b-S5 ergänzt: `PUT /config` mit niedriger Wortobergrenze → Upload eines Dokuments → `status="skipped"` sichtbar über `GET /ocr-results`.
- **Live-E2E für die Textlayer-Einbettung** (Nutzer-Feedback-Durchgang): eine synthetische Scan-PDF (Bild mit Text, kein nativer Textlayer) real hochgeladen — `tesseract`-Engine erkannte das Wort, es entstand automatisch genau eine neue Version (`created_by="system:ocr-service"`, `comment="OCR: durchsuchbarer Textlayer eingebettet"`), deren Inhalt per `fitz` nachweislich extrahierbaren Text enthielt, während die sichtbaren Pixel unverändert blieben. Die real beim ersten Versuch beobachtete Fehlfunktion (drei statt zwei Versionen durch den oben beschriebenen Reprocessing-Bug) wurde nach dem Fix erneut real verifiziert — genau zwei Versionen. Ein hochgeladenes Bild wurde mit dem neuen Default (`allowed_content_types=["application/pdf"]`) korrekt mit `status="skipped"` übersprungen; nach expliziter Admin-Freigabe (`PUT /config`) lief OCR real dafür, ohne eine neue Version zu erzeugen (Rückfrage-Entscheidung: Bilder behalten ihr Format).

## Offene Punkte

- **PaddleOCR nicht implementiert**: nur die Plugin-Schnittstelle lässt es zu, siehe ADR 0011.
- **Textlayer-Verfügbarkeit wird nur anhand Seite 1 entschieden**: `select_engine()` prüft nicht seitenweise, ob ein nutzbarer Textlayer existiert — ein PDF mit z. B. nativer Seite 1 und gescannter Seite 2 bekäme fälschlich `NativeTextLayerEngine` für das gesamte Dokument (Seite 2 hätte dann keine erkannten Wörter). Bewusste Vereinfachung, kein bekannter Anwendungsfall dafür bisher.
- **`needs_review` ohne echte Workflow-Anbindung**: BPMN-gestützte manuelle Nachprüfung folgt frühestens mit P6-S1/P6-S4.
- **Keine automatische Nachverarbeitung bei dauerhaftem `failed`**: kein Retry-Mechanismus, analog zu rendering-service.
- **Keine Autorisierung** (wie bei allen bisherigen Services): Gateway prüft nur Token-Gültigkeit, keine Rollenprüfung.
- **Wortobergrenze ist eine grobe Schätzung** (P5b-S5): `Seitenzahl × 250` statt exakter Zählung — kann bei textarmen mehrseitigen PDFs zu früh und bei textdichten Einzelbildern nie greifen (Details/Begründung siehe ADR 0016).
- **Batch-Size begrenzt nur die Anzahl gleichzeitiger Aufrufe, nicht den Ressourcenverbrauch je Aufruf** — kein echter Worker-Pool mit Speicher-/CPU-Accounting.
- **`ocrEnabled` ist nur als Compose-Profil sichtbar/steuerbar** — die Admin-UI zeigt lediglich "erreichbar"/"nicht erreichbar", kein Schalter (Begründung siehe ADR 0016).
- **Textlayer-Einbettung bei sehr kurzem erkanntem Text (unter 20 Zeichen) bleibt im Overlay-Fallback hängen** — siehe "Durchsuchbare PDF" oben für die Begründung; betrifft praktisch keine realen mehrzeiligen Scans.
- **Kein Retry/keine Vier-Augen-Prüfung für die Textlayer-Versionierung selbst** — ein fehlgeschlagener `create_version()`-Aufruf (z. B. Netzwerkfehler) wird nur geloggt, der Scan bleibt dauerhaft ohne durchsuchbare Version, bis eine erneute Verarbeitung (z. B. durch einen künftigen Retry-Mechanismus, siehe oben) sie nachholt.
