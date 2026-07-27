# user-ui

**Verantwortung:** Authentifizierte Web-Oberfläche für Endnutzer — Anmelden, Ordner-Navigation inkl. CRUD, Dokument-Upload/-Download (versionsbewusst seit P5-S3), Dokumentmetadaten-Bearbeitung, Vorschau mit Thumbnail und OCR-Text-Overlay, Volltext-/Facettensuche (seit P5-S4) (Konzept 8).
**Konzept-Referenz:** 8
**Kein eigenes Postgres-Schema** — reine clientseitig gerenderte SPA (statischer Export, siehe [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), kein eigener Backend-Prozess.

## Ort im Repo

`apps/user-ui/` — bewusst **nicht** unter `services/` (das Python-Service-Template passt nicht auf eine Node/React-Toolchain), siehe ADR 0006.

## Seiten

| Route | Zweck |
|---|---|
| `/login/` | Anmeldung (Benutzername/Passwort gegen den Auth Service über das Gateway) |
| `/` | `DocumentWorkspace` (seit P4-S4) — nur erreichbar mit gültiger Session (clientseitiger Redirect nach `/login/`, wenn nicht angemeldet) |

## Layout (P4-S4, Nutzer-Feedback nach dem ersten echten Browser-Test des MVP)

Ersetzt die frühere flache `FolderBrowser`-Ansicht (P4-S2) durch ein dreigeteiltes, resizable Arbeitsbereichs-Layout gemäß Konzept 8:

- **`IconRail`** (ganz links, außerhalb des Main-Contents): iconbasierte Cross-Cutting-Navigation. "Dokumente" und seit P5-S4 auch "Suche" schalten zwischen den beiden Ansichten von `DocumentWorkspace` um (`activeView`/`onSelectView`) — zuvor war "Suche" ein bewusst sichtbarer, deaktivierter Platzhalter. "Einstellungen" öffnet seit P4-S6 ein Popover mit dem `ThemeSwitcher` statt weiter deaktiviert zu sein.
- **`ExplorerPane`** (oben links, Ansicht "Dokumente"): Windows-Explorer-artige Ordnernavigation mit Breadcrumb, Ordner-CRUD (Anlegen/Umbenennen/Löschen — die Backend-Endpunkte existierten bereits seit P3-S3, nur die UI dafür fehlte) und einer Tableiste für geöffnete Dokumente. Klick auf ein Dokument öffnet es als Tab, statt einen modalen Dialog zu zeigen.
- **`MetadataPanel`** (unten links, Ansicht "Dokumente"): Metadaten des über die Tabs ausgewählten Dokuments. Attribut-Formfelder werden dynamisch aus dem Objekttyp-Schema generiert (`GET /api/object-type-service/object-types/{id}`, 2.2) — ohne zugewiesenen Objekttyp ist nur der Titel editierbar. Speichert über den seit P4-S4 neuen `PATCH /api/document-service/documents/{id}`.
- **`SearchPane`** (linke Spalte, Ansicht "Suche", neu seit P5-S4): ersetzt `ExplorerPane`/`MetadataPanel` im Suchmodus. Suchfeld + Objekttyp-Auswahl, die passende Attributfilter einblendet (Bereichsfilter bei `date`/`decimal`/`integer`, Exakt-Match sonst). Klick auf ein Ergebnis öffnet es über dieselbe `openDocumentTab()`-Funktion wie ein Dokument aus dem Explorer — Tab-Leiste/Metadaten/Vorschau funktionieren unverändert, kein separater Codepfad für Suchtreffer.
- **`PreviewPane`** (rechts): vom aktiven Tab synchronisiert, **unabhängig von der aktuell gewählten Ansicht** (Dokumente/Suche) — ein aus der Suche geöffnetes Ergebnis zeigt seine Vorschau sofort, auch ohne in die Dokumente-Ansicht zurückzuwechseln (bewusst kein automatischer View-Wechsel beim Öffnen eines Suchtreffers). Lädt seit P5-S2 die vom Rendering Service erzeugte Thumbnail-Ersatzdarstellung nach und zeigt sie als Bild an, sofern eine mit Status `ready` existiert. Seit P5-S3 zusätzlich: Versionsauswahl (`<select>`, nur sichtbar bei mehr als einer Version) sowie ein positionsgenaues Text-Overlay aus den Wort-Bounding-Boxen des OCR Service, mit dem sich erkannter Text direkt über dem Bild markieren/kopieren lässt (Nutzer-Feedback nach P5-S2).

Alle drei Content-Spalten sind über `Splitter`-Ziehgriffe größenveränderbar; die Aufteilung wird pro Browser in `localStorage` gemerkt (`dms.explorer.leftWidth`/`dms.explorer.topHeight`). `Splitter` ist eine generische, abhängigkeitsfreie Komponente (Pointer-Events, Neuberechnung relativ zur Container-Bounding-Box bei jedem Move statt kumulierter Deltas) — keine externe Layout-Bibliothek eingeführt, um die Abhängigkeitsfläche klein zu halten.

## Anbindung an das Backend

Ausschließlich über das API-Gateway (3.5, `/api/{service_type}/{path}`), keine direkten Aufrufe einzelner Backend-Services:

| Aktion | Gateway-Aufruf |
|---|---|
| Anmelden | `POST /api/auth-service/login` (öffentliche Route, kein Token nötig) |
| Identität nach Login | `GET /api/auth-service/me` |
| Ordner-Navigation | `GET /api/folder-service/folders/{id}/children` (Start: `root`) |
| Ordner anlegen/umbenennen/löschen | `POST /api/folder-service/folders`, `PATCH /api/folder-service/folders/{id}`, `DELETE /api/folder-service/folders/{id}` (seit P4-S4 in der UI verdrahtet) |
| Dokumente eines Ordners | `GET /api/document-service/documents?folder_id={id}` |
| Hochladen | `POST /api/document-service/documents` (multipart) |
| Herunterladen | `GET /api/document-service/documents/{id}/content` |
| Dokumentmetadaten ändern | `PATCH /api/document-service/documents/{id}` (seit P4-S4, neuer Endpunkt) |
| Objekttyp-Schema für das Metadaten-Panel | `GET /api/object-type-service/object-types/{id}` |
| Theme-Präferenz lesen/schreiben | `GET/PUT /api/auth-service/me/preferences` (seit P4-S6) |
| Ersatzdarstellungen zur ausgewählten Version | `GET /api/rendering-service/renditions?document_id={id}&version_number={n}` (seit P5-S2, `version_number` seit P5-S3) |
| Vorschaubild laden | `GET /api/rendering-service/renditions/{id}/content` (seit P5-S2) |
| Versionshistorie eines Dokuments | `GET /api/document-service/documents/{id}/versions` (seit P5-S3) |
| Bestimmte Version herunterladen | `GET /api/document-service/documents/{id}/versions/{n}/content` (seit P5-S3) |
| OCR-Ergebnis zur ausgewählten Version (Wort-Bounding-Boxen) | `GET /api/ocr-service/ocr-results?document_id={id}&version_number={n}` (seit P5-S3) |
| OCR-eigenes PDF-Seitenbild | `GET /api/ocr-service/ocr-results/{id}/page-image` (seit P5-S3) |
| Facetten-Definitionen für die Suche | `GET /api/search-service/search/facets` (seit P5-S4) |
| Suche + Facettenfilter | `GET /api/search-service/search?...` (seit P5-S4) |

## Auth-Zustand

`src/lib/auth-context.tsx`: Access-/Refresh-Token im `localStorage` (`dms.tokens`), proaktiver Refresh kurz vor Ablauf (`setTimeout` basierend auf `expires_in`). Bekannte, bewusste Vereinfachung dieses Grundgerüsts: kein httpOnly-Cookie, siehe ADR 0006 "Offene Punkte"/Konsequenzen.

## Vorschau (2.4/3.9, seit P5-S2 an den Rendering Service, seit P5-S3 zusätzlich an den OCR Service angebunden)

`components/PreviewPane.tsx` lädt beim Öffnen eines Dokuments (`useEffect`, Abhängigkeit `activeDocument?.id`) zunächst die Versionsliste (`listDocumentVersions()`) und setzt die Auswahl auf `current_version_number`; ein zweiter Effekt (Abhängigkeit `[activeDocument?.id, selectedVersion]`) lädt für die ausgewählte Version sowohl das OCR-Ergebnis (`listOcrResults()`) als auch das Anzeigebild:

- Existiert ein OCR-Ergebnis, wird zuerst dessen eigenständiges Seitenbild versucht (`downloadOcrPageImage()`, nur für PDFs — der OCR Service rastert diese selbst, da rendering-service keine PDF-Thumbnails erzeugt).
- Schlägt das fehl oder existiert kein OCR-Ergebnis, greift die bestehende `thumbnail`-Rendition aus rendering-service (`listRenditions()`/`downloadRenditionContent()`, Rasterbild-Fall).

Beide Pfade zeigen das Ergebnis als `<img>` über eine per `URL.createObjectURL` erzeugte Blob-URL an — beim Wechsel/Unmount wird die vorherige Blob-URL wieder freigegeben (`URL.revokeObjectURL`). Existiert ein `ready`/`needs_review`-OCR-Ergebnis, wird zusätzlich ein **Text-Overlay** aus dessen Wort-Bounding-Boxen gerendert: ein `<div>` je Wort, unsichtbar (`color: transparent`) und prozentual exakt über dem Bild positioniert (`left/top/width/height` als `%` von `page.width`/`page.height`, gleiches Prinzip wie pdf.js' Textlayer) — Nutzer können den erkannten Text direkt über dem Dokumentbild markieren und kopieren. Die Schriftgröße jedes Worts wird über einen `ResizeObserver` auf das `<img>` an die tatsächlich gerenderte Bildhöhe angepasst, damit Markieren/Doppelklick-Wortauswahl bei jeder Splitter-Breite/jedem Zoom treffsicher bleiben. Ein `needs_review`-Ergebnis zeigt zusätzlich einen kleinen Warnhinweis, blendet das Overlay aber nicht aus (der Text bleibt nutzbar).

Renditions/OCR-Ergebnisse sind bewusst ein Zusatznutzen, kein Blocker (2.4/3.9): existiert nichts Passendes (falsches Format, Verarbeitung noch nicht abgeschlossen, Ladefehler), fällt die Spalte auf einen Hinweistext zurück — der Download-Button (jetzt versionsbewusst, `downloadDocumentVersion()`) bleibt in jedem Fall nutzbar. Absichtlich einfach gehalten: kein Polling auf eine noch nicht fertige Verarbeitung (erneutes Öffnen/Versionswechsel holt den aktuellen Stand nach), nur Seite 1 bei mehrseitigen PDFs (konsistent mit dem OCR-Service-Scope).

## Suche (3.7, neu seit P5-S4)

`IconRail` bekam einen zweiten aktivierbaren Button ("Suche" neben "Dokumente"), gesteuert über einen neuen `view`-State in `DocumentWorkspace` (`"documents" | "search"`). Im Suchmodus ersetzt die neue `components/SearchPane.tsx` `ExplorerPane`/`MetadataPanel` in der linken Spalte:

- Lädt einmalig beim Mount die Facetten-Definitionen (`getSearchFacets()`) — verfügbare Objekttypen inkl. Attributschema.
- Auswahl eines Objekttyps blendet passende Attributfilter-Controls ein: `date`/`decimal`/`integer` bekommen zwei Bereichsfelder (von/bis), `string`/`reference`/`boolean` ein einzelnes Textfeld für Exakt-Match — Typzuordnung direkt aus dem Objekttyp-Schema (`ObjectTypeAttribute.type`), dieselbe Konvention wie das Metadaten-Panel.
- Suche (`searchDocuments()`) baut die Backend-Query-Parameter-Konvention (`attr.{name}`/`attr.{name}.gte`/`.lte`) aus den Filterwerten zusammen — die genaue Zuordnung Frontend-State → Query-Parameter bleibt in `SearchPane.tsx` gekapselt, `api.ts`s `searchDocuments()` reicht bereits fertige Schlüssel nur durch.
- Klick auf ein Ergebnis ruft dieselbe `openDocumentTab()`-Funktion wie das Öffnen eines Dokuments aus `ExplorerPane` — ein Suchergebnis (`SearchResult`) ist eine strukturelle Erweiterung von `DocumentSummary` (zusätzlich `folder_name`, `rank`, `snippet`), braucht also keinen separaten Öffnen-Codepfad. `PreviewPane` reagiert unabhängig vom aktiven `view` immer auf `activeTabId` — ein aus der Suche geöffnetes Dokument zeigt seine Vorschau sofort, auch ohne zurück in die Dokumente-Ansicht zu wechseln (bewusst kein automatischer View-Wechsel, da die bestehende Tableiste ohnehin nur im Dokumente-Modus sichtbar ist — ein Wechsel würde die gerade genutzte Suchansicht unerwartet verdecken).
- Snippet-Anzeige ist reiner Text ohne Hervorhebungs-Markup — `ts_headline`s HTML-Ausgabe bräuchte ein Sanitizing, das an keiner Stelle dieser Codebasis existiert; bewusst nicht eingeführt für diese Session.

## Theming (Konzept 8, seit P4-S6)

`src/lib/theme-context.tsx` (`ThemeProvider`/`useTheme()`): Hell/Dunkel/Hoher-Kontrast/Automatisch, umschaltbar über den `ThemeSwitcher` im Einstellungen-Popover der `IconRail`. Geräteübergreifend am Nutzerkonto gespeichert (`GET/PUT /api/auth-service/me/preferences`), siehe [ADR 0009](../adr/0009-cross-ui-theming-profile-persistence.md) für die Begründung (Keycloak-Attribut statt neuer Persistenz-Baustein) und den dabei gefundenen Stolperstein (Declarative User Profile verwirft nicht deklarierte Attribute stillschweigend). `localStorage` (`dms.theme`) dient als sofort verfügbarer Cache, u. a. damit die Login-Seite ohne Sitzung ebenfalls ein Theme hat. `data-theme` auf `<html>` steuert per CSS-Variablen (`--dms-bg`, `--dms-fg`, `--dms-border`, `--dms-accent`, ...) das gesamte Stylesheet (`globals.css`).

## Internationalisierung (Konzept 8, seit P4-S3)

Alle sichtbaren Texte liegen in `src/i18n/de.json`, aufgelöst über `useI18n()`/`t("bereich.schlüssel")` (siehe [ADR 0007](../adr/0007-frontend-i18n-preparation.md)). Aktiv ist ausschließlich Deutsch, aber ohne weitere Komponenten-Änderungen erweiterbar — eine zweite Sprache ist nur eine zusätzliche JSON-Datei plus Registrierung in `src/i18n/index.tsx`. Noch keine Sprachumschaltung in der UI.

## Build & Auslieferung

Zweistufiges Docker-Image (`apps/user-ui/Dockerfile`): Node nur im Build-Stage (`next build` mit `output: "export"`), Laufzeit-Image ist `nginx:alpine` ohne Node-Prozess. Die Gateway-Adresse (`NEXT_PUBLIC_GATEWAY_BASE_URL`) wird als Build-Arg fest eingebrannt (kein Server, der sie zur Laufzeit nachladen könnte) — überschreibbar über `USER_UI_GATEWAY_BASE_URL` in `infra/.env`.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build` — Typprüfung, ESLint, produktionsfähiger statischer Export.
- `npm test` (Vitest + Testing Library, **29 Tests**): `AuthProvider` (Login/Logout/Session-Wiederherstellung/Ablauf), API-Client (Gateway-URL-Aufbau, Bearer-Header, Fehlerbehandlung, seit P4-S4 auch Ordner-CRUD/Metadaten-PATCH), `DocumentWorkspace` (Navigation, Ordner-CRUD, Tab-Öffnen inkl. Vorschau-Synchronisation, Metadaten-Speichern inkl. Tab-Titel-Update, Upload-Reload, seit P5-S2 auch: keine Rendition vorhanden → Hinweistext, `ready`-Thumbnail → `<img>` mit Blob-URL; seit P5-S3 zusätzlich: OCR-Wort-Spans mit korrekten Prozent-Positionen, kein Overlay ohne bereites OCR-Ergebnis, Versionswechsel löst Neuladen von Renditions/OCR mit der gewählten Versionsnummer aus, Download nutzt die ausgewählte statt der aktuellen Version; seit P5-S4 zusätzlich: View-Umschaltung Dokumente↔Suche über `IconRail`, Suche + Ergebnisklick öffnet einen Dokument-Tab, Attributfilter-Controls je Objekttyp-Attributtyp, Leerzustand ohne Treffer), `ThemeProvider` (seit P4-S6: Default `auto`, `data-theme`-Attribut, `localStorage`-Cache-Wiederherstellung, `setTheme`-Persistenz) — Netzwerkschicht (`fetch`) gemockt, da sie die Grenze zur externen Infrastruktur ist. `matchMedia` und (seit P5-S3) `ResizeObserver` werden in `tests/setup.ts` gepolyfillt, da jsdom sie nicht implementiert.
- **Kein Browser für visuelle/E2E-Tests in dieser Entwicklungsumgebung verfügbar** (kein installiertes Chrome/Chromium, Playwright daher nicht einsetzbar) — stattdessen wurde jeder von der UI verwendete Gateway-Aufruf einzeln per `curl` gegen den echten laufenden Compose-Stack nachvollzogen (Login → Ordner anlegen/umbenennen → Upload in den neuen Ordner → Metadaten-PATCH ändert Titel/Attribute → Liste zeigt die Änderung → Ordner löschen → Objekttyp-Schema über das Gateway abrufbar; seit P4-S6 zusätzlich: `GET/PUT /me/preferences` inkl. 422 bei ungültigem Theme-Wert; seit P5-S2 zusätzlich: `GET /renditions`/`GET /renditions/{id}/content` liefern ein echtes, vom Rendering Service erzeugtes Thumbnail; seit P5-S3 zusätzlich: ein echtes PDF mit Textlayer hochgeladen → `GET /ocr-results` liefert korrekte Wort-Bounding-Boxen, `GET /ocr-results/{id}/page-image` liefert ein passendes PNG, `GET /documents/{id}/versions` liefert die Versionshistorie; seit P5-S4 zusätzlich: `GET /search?q=...` findet ein echtes indiziertes Dokument mit korrektem Ranking/Snippet, `401` ohne `X-DMS-Principal`-Header, ein Principal ohne Ordner-Leserecht sieht das Dokument nicht, nach einer echten Rollenzuweisung über den Permission Service erscheint es). Die neue Resizable-/Tab-/Layout-Interaktion, das Theme-Umschalten, die Thumbnail-Anzeige, das OCR-Text-Overlay sowie die Such-/Filter-UI selbst konnten dadurch **nicht visuell** verifiziert werden — nur über die Vitest-Komponententests plus die curl-bestätigte Korrektheit der zugrunde liegenden API-Antworten. Ein Mensch sollte die Oberfläche vor einer Produktivnutzung im echten Browser durchklicken.

## Offene Punkte

- Tokens im `localStorage` statt httpOnly-Cookie (XSS-Risiko bewusst in Kauf genommen, siehe ADR 0006).
- Vorschau zeigt weiterhin nur Bild + OCR-Text-Overlay an, keine eigene Ansicht für `substitute_text`/`pdf_archive`-Renditionen — wären ohnehin eher Download- als Vorschau-Kandidaten.
- Kein Polling, falls eine Ersatzdarstellung/ein OCR-Ergebnis zum Zeitpunkt des Öffnens noch nicht fertig verarbeitet ist — erneutes Öffnen des Tabs/Versionswechsel holt den aktuellen Stand nach, kein automatisches Nachladen.
- Versionshistorie zeigt nur die Versionsnummer (kein Diff, kein Kommentar-/Konfliktanzeige-UI trotz vorhandener Backend-Felder wie `is_conflict`/`comment`) — bewusst minimal, da nicht Teil des Nutzerwunsches dieser Session.
- OCR-Overlay nur für Seite 1 mehrseitiger PDFs (konsistent mit dem OCR-Service-Scope, siehe `docs/services/ocr-service.md`).
- Such-Snippet ohne Hervorhebungs-Markup (kein Sanitizing in dieser Codebasis vorhanden, siehe Suche-Abschnitt oben).
- Kein automatischer View-Wechsel beim Öffnen eines Suchtreffers — die Tableiste bleibt bis zum manuellen Zurückwechseln in die Dokumente-Ansicht unsichtbar, nur die Vorschau reagiert sofort.
- Keine Workflow-Interaktion (Freigaben/Aufgaben) — Workflow Engine existiert erst ab Phase 6.
- Kein automatisiertes Browser-E2E in dieser Umgebung möglich (kein Chrome/Chromium installiert) — nachzuholen, sobald eine Umgebung mit Browser verfügbar ist (z. B. CI).
- Rollenabhängige Ansichten/Branding (Konzept 8, "Anpassbarkeit") nicht Teil dieses Grundgerüsts.
- i18n nur strukturell vorbereitet (ADR 0007), keine zweite Sprache und keine UI-Sprachumschaltung.
- Theme-Präferenz hat keinen Konfliktauflösungsmechanismus zwischen Geräten (letzter Fetch gewinnt) und kein Retry bei fehlgeschlagenem `PUT /me/preferences` (siehe ADR 0009 "Konsequenzen").
- Ordner-Verschieben (neuer Elternordner) ist im Backend (`PATCH /folders/{id}`) bereits möglich, in der UI aber bewusst nicht verdrahtet — Umbenennen deckt den in dieser Session geforderten CRUD-Umfang ab, ein Drag&Drop-Verschieben ist eine spätere UX-Verfeinerung.
- Bekannte Backend-Lücke, bei dieser Session sichtbar geworden: `DELETE /folders/{id}` prüft nur auf Unterordner, nicht auf enthaltene Dokumente — ein Ordner mit nur Dokumenten (keinen Unterordnern) lässt sich aktuell löschen, ohne dass die Dokumente mitgelöscht oder die Löschung blockiert wird (deren `folder_id` bliebe auf eine nicht mehr existierende Ressource verweisen). Nicht in dieser Session behoben, siehe `PROGRESS.md`.
