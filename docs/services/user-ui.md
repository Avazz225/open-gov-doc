# user-ui

**Verantwortung:** Authentifizierte Web-Oberfläche für Endnutzer — Anmelden, Ordner-Navigation inkl. CRUD, Dokument-Upload/-Download, Dokumentmetadaten-Bearbeitung, Vorschau-Platzhalter (Konzept 8).
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

- **`IconRail`** (ganz links, außerhalb des Main-Contents): iconbasierte Cross-Cutting-Navigation. "Dokumente" ist funktional, "Suche" (P5-S4) bleibt ein bewusst sichtbarer, deaktivierter Platzhalter. "Einstellungen" öffnet seit P4-S6 ein Popover mit dem `ThemeSwitcher` statt weiter deaktiviert zu sein.
- **`ExplorerPane`** (oben links): Windows-Explorer-artige Ordnernavigation mit Breadcrumb, Ordner-CRUD (Anlegen/Umbenennen/Löschen — die Backend-Endpunkte existierten bereits seit P3-S3, nur die UI dafür fehlte) und einer Tableiste für geöffnete Dokumente. Klick auf ein Dokument öffnet es als Tab, statt einen modalen Dialog zu zeigen.
- **`MetadataPanel`** (unten links): Metadaten des über die Tabs ausgewählten Dokuments. Attribut-Formfelder werden dynamisch aus dem Objekttyp-Schema generiert (`GET /api/object-type-service/object-types/{id}`, 2.2) — ohne zugewiesenen Objekttyp ist nur der Titel editierbar. Speichert über den seit P4-S4 neuen `PATCH /api/document-service/documents/{id}`.
- **`PreviewPane`** (rechts): vom aktiven Tab synchronisiert, zeigt weiterhin nur einen Stub (echtes Rendering folgt mit dem Preview Service, P5-S2) — jetzt aber als permanenter Bereich statt als Overlay.

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

## Auth-Zustand

`src/lib/auth-context.tsx`: Access-/Refresh-Token im `localStorage` (`dms.tokens`), proaktiver Refresh kurz vor Ablauf (`setTimeout` basierend auf `expires_in`). Bekannte, bewusste Vereinfachung dieses Grundgerüsts: kein httpOnly-Cookie, siehe ADR 0006 "Offene Punkte"/Konsequenzen.

## Vorschau (2.4)

`components/PreviewPane.tsx` zeigt nur einen Hinweis ("Vorschau ist noch nicht verfügbar") statt einer echten Vorschau — der Rendering/Preview Service (3.7) existiert erst ab P5-S2. Seit P4-S4 ist das ein fest im Layout verankerter Bereich (vorher ein modaler Dialog, `PreviewStub`) — bewusst weiterhin isoliert, damit er später durch eine echte Vorschau ersetzt werden kann, ohne den Rest des Layouts anzufassen.

## Theming (Konzept 8, seit P4-S6)

`src/lib/theme-context.tsx` (`ThemeProvider`/`useTheme()`): Hell/Dunkel/Hoher-Kontrast/Automatisch, umschaltbar über den `ThemeSwitcher` im Einstellungen-Popover der `IconRail`. Geräteübergreifend am Nutzerkonto gespeichert (`GET/PUT /api/auth-service/me/preferences`), siehe [ADR 0009](../adr/0009-cross-ui-theming-profile-persistence.md) für die Begründung (Keycloak-Attribut statt neuer Persistenz-Baustein) und den dabei gefundenen Stolperstein (Declarative User Profile verwirft nicht deklarierte Attribute stillschweigend). `localStorage` (`dms.theme`) dient als sofort verfügbarer Cache, u. a. damit die Login-Seite ohne Sitzung ebenfalls ein Theme hat. `data-theme` auf `<html>` steuert per CSS-Variablen (`--dms-bg`, `--dms-fg`, `--dms-border`, `--dms-accent`, ...) das gesamte Stylesheet (`globals.css`).

## Internationalisierung (Konzept 8, seit P4-S3)

Alle sichtbaren Texte liegen in `src/i18n/de.json`, aufgelöst über `useI18n()`/`t("bereich.schlüssel")` (siehe [ADR 0007](../adr/0007-frontend-i18n-preparation.md)). Aktiv ist ausschließlich Deutsch, aber ohne weitere Komponenten-Änderungen erweiterbar — eine zweite Sprache ist nur eine zusätzliche JSON-Datei plus Registrierung in `src/i18n/index.tsx`. Noch keine Sprachumschaltung in der UI.

## Build & Auslieferung

Zweistufiges Docker-Image (`apps/user-ui/Dockerfile`): Node nur im Build-Stage (`next build` mit `output: "export"`), Laufzeit-Image ist `nginx:alpine` ohne Node-Prozess. Die Gateway-Adresse (`NEXT_PUBLIC_GATEWAY_BASE_URL`) wird als Build-Arg fest eingebrannt (kein Server, der sie zur Laufzeit nachladen könnte) — überschreibbar über `USER_UI_GATEWAY_BASE_URL` in `infra/.env`.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build` — Typprüfung, ESLint, produktionsfähiger statischer Export.
- `npm test` (Vitest + Testing Library, **20 Tests**): `AuthProvider` (Login/Logout/Session-Wiederherstellung/Ablauf), API-Client (Gateway-URL-Aufbau, Bearer-Header, Fehlerbehandlung, seit P4-S4 auch Ordner-CRUD/Metadaten-PATCH), `DocumentWorkspace` (Navigation, Ordner-CRUD, Tab-Öffnen inkl. Vorschau-Synchronisation, Metadaten-Speichern inkl. Tab-Titel-Update, Upload-Reload), `ThemeProvider` (seit P4-S6: Default `auto`, `data-theme`-Attribut, `localStorage`-Cache-Wiederherstellung, `setTheme`-Persistenz) — Netzwerkschicht (`fetch`) gemockt, da sie die Grenze zur externen Infrastruktur ist. `matchMedia` wird in `tests/setup.ts` gepolyfillt, da jsdom es nicht implementiert.
- **Kein Browser für visuelle/E2E-Tests in dieser Entwicklungsumgebung verfügbar** (kein installiertes Chrome/Chromium, Playwright daher nicht einsetzbar) — stattdessen wurde jeder von der UI verwendete Gateway-Aufruf einzeln per `curl` gegen den echten laufenden Compose-Stack nachvollzogen (Login → Ordner anlegen/umbenennen → Upload in den neuen Ordner → Metadaten-PATCH ändert Titel/Attribute → Liste zeigt die Änderung → Ordner löschen → Objekttyp-Schema über das Gateway abrufbar; seit P4-S6 zusätzlich: `GET/PUT /me/preferences` inkl. 422 bei ungültigem Theme-Wert). Die neue Resizable-/Tab-/Layout-Interaktion sowie das Theme-Umschalten selbst (Ziehgriffe, Tab-Wechsel-Optik, Popover, Flash-freier Themewechsel) konnten dadurch **nicht visuell** verifiziert werden — nur über die Vitest-Komponententests. Ein Mensch sollte die Oberfläche vor einer Produktivnutzung im echten Browser durchklicken.

## Offene Punkte

- Tokens im `localStorage` statt httpOnly-Cookie (XSS-Risiko bewusst in Kauf genommen, siehe ADR 0006).
- Keine echte Vorschau (folgt P5-S2).
- Keine Suche (Konzept 8 nennt sie, Search Service existiert erst P5-S4).
- Keine Workflow-Interaktion (Freigaben/Aufgaben) — Workflow Engine existiert erst ab Phase 6.
- Kein automatisiertes Browser-E2E in dieser Umgebung möglich (kein Chrome/Chromium installiert) — nachzuholen, sobald eine Umgebung mit Browser verfügbar ist (z. B. CI).
- Rollenabhängige Ansichten/Branding (Konzept 8, "Anpassbarkeit") nicht Teil dieses Grundgerüsts.
- i18n nur strukturell vorbereitet (ADR 0007), keine zweite Sprache und keine UI-Sprachumschaltung.
- Theme-Präferenz hat keinen Konfliktauflösungsmechanismus zwischen Geräten (letzter Fetch gewinnt) und kein Retry bei fehlgeschlagenem `PUT /me/preferences` (siehe ADR 0009 "Konsequenzen").
- Ordner-Verschieben (neuer Elternordner) ist im Backend (`PATCH /folders/{id}`) bereits möglich, in der UI aber bewusst nicht verdrahtet — Umbenennen deckt den in dieser Session geforderten CRUD-Umfang ab, ein Drag&Drop-Verschieben ist eine spätere UX-Verfeinerung.
- Bekannte Backend-Lücke, bei dieser Session sichtbar geworden: `DELETE /folders/{id}` prüft nur auf Unterordner, nicht auf enthaltene Dokumente — ein Ordner mit nur Dokumenten (keinen Unterordnern) lässt sich aktuell löschen, ohne dass die Dokumente mitgelöscht oder die Löschung blockiert wird (deren `folder_id` bliebe auf eine nicht mehr existierende Ressource verweisen). Nicht in dieser Session behoben, siehe `PROGRESS.md`.
