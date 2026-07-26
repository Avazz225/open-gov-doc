# user-ui

**Verantwortung:** Authentifizierte Web-Oberfläche für Endnutzer — Anmelden, Ordner-Navigation, Dokument-Upload/-Download, Vorschau-Platzhalter (Konzept 8).
**Konzept-Referenz:** 8
**Kein eigenes Postgres-Schema** — reine clientseitig gerenderte SPA (statischer Export, siehe [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), kein eigener Backend-Prozess.

## Ort im Repo

`apps/user-ui/` — bewusst **nicht** unter `services/` (das Python-Service-Template passt nicht auf eine Node/React-Toolchain), siehe ADR 0006.

## Seiten

| Route | Zweck |
|---|---|
| `/login/` | Anmeldung (Benutzername/Passwort gegen den Auth Service über das Gateway) |
| `/` | Ordner-Browser: Navigation, Upload, Download, Vorschau-Platzhalter — nur erreichbar mit gültiger Session (clientseitiger Redirect nach `/login/`, wenn nicht angemeldet) |

## Anbindung an das Backend

Ausschließlich über das API-Gateway (3.5, `/api/{service_type}/{path}`), keine direkten Aufrufe einzelner Backend-Services:

| Aktion | Gateway-Aufruf |
|---|---|
| Anmelden | `POST /api/auth-service/login` (öffentliche Route, kein Token nötig) |
| Identität nach Login | `GET /api/auth-service/me` |
| Ordner-Navigation | `GET /api/folder-service/folders/{id}/children` (Start: `root`) |
| Dokumente eines Ordners | `GET /api/document-service/documents?folder_id={id}` (neuer Endpunkt, seit dieser Session — siehe `docs/services/document-service.md`) |
| Hochladen | `POST /api/document-service/documents` (multipart) |
| Herunterladen | `GET /api/document-service/documents/{id}/content` |

## Auth-Zustand

`src/lib/auth-context.tsx`: Access-/Refresh-Token im `localStorage` (`dms.tokens`), proaktiver Refresh kurz vor Ablauf (`setTimeout` basierend auf `expires_in`). Bekannte, bewusste Vereinfachung dieses Grundgerüsts: kein httpOnly-Cookie, siehe ADR 0006 "Offene Punkte"/Konsequenzen.

## Vorschau (2.4)

`components/PreviewStub.tsx` zeigt nur einen Hinweis-Dialog ("Vorschau ist noch nicht verfügbar") statt einer echten Vorschau — der Rendering/Preview Service (3.7) existiert erst ab P5-S2. Die Komponente ist bewusst isoliert, damit sie später durch eine echte Vorschau ersetzt werden kann, ohne den Ordner-Browser selbst anzufassen.

## Build & Auslieferung

Zweistufiges Docker-Image (`apps/user-ui/Dockerfile`): Node nur im Build-Stage (`next build` mit `output: "export"`), Laufzeit-Image ist `nginx:alpine` ohne Node-Prozess. Die Gateway-Adresse (`NEXT_PUBLIC_GATEWAY_BASE_URL`) wird als Build-Arg fest eingebrannt (kein Server, der sie zur Laufzeit nachladen könnte) — überschreibbar über `USER_UI_GATEWAY_BASE_URL` in `infra/.env`.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build` — Typprüfung, ESLint, produktionsfähiger statischer Export.
- `npm test` (Vitest + Testing Library): `AuthProvider` (Login/Logout/Session-Wiederherstellung/Ablauf), API-Client (Gateway-URL-Aufbau, Bearer-Header, Fehlerbehandlung), `FolderBrowser` (Navigation, Upload-Reload, Vorschau-Stub) — Netzwerkschicht (`fetch`) gemockt, da sie die Grenze zur externen Infrastruktur ist (analog zu `dms-auth-client`s lokalen Test-Schlüsseln statt echtem Keycloak).
- **Kein Browser für visuelle/E2E-Tests in dieser Entwicklungsumgebung verfügbar** (kein installiertes Chrome/Chromium, Playwright daher nicht einsetzbar) — stattdessen wurde jeder von der UI verwendete Gateway-Aufruf einzeln per `curl` gegen den echten laufenden Compose-Stack nachvollzogen (Login → `/me` → Ordner-Navigation inkl. neu angelegtem Unterordner → Upload → Liste zeigt neues Dokument → Download liefert exakt die hochgeladenen Bytes zurück). Ein Mensch sollte die Oberfläche vor einer Produktivnutzung dennoch einmal im echten Browser durchklicken.

## Offene Punkte

- Tokens im `localStorage` statt httpOnly-Cookie (XSS-Risiko bewusst in Kauf genommen, siehe ADR 0006).
- Keine echte Vorschau (folgt P5-S2).
- Keine Suche (Konzept 8 nennt sie, Search Service existiert erst P5-S4).
- Keine Workflow-Interaktion (Freigaben/Aufgaben) — Workflow Engine existiert erst ab Phase 6.
- Kein automatisiertes Browser-E2E in dieser Umgebung möglich (kein Chrome/Chromium installiert) — nachzuholen, sobald eine Umgebung mit Browser verfügbar ist (z. B. CI).
- Rollenabhängige Ansichten/Branding (Konzept 8, "Anpassbarkeit") nicht Teil dieses Grundgerüsts.
