# admin-ui

**Verantwortung:** Administrative Web-Oberfläche — Nutzer-/Rollenverwaltung, Objekttyp-Editor, Registry-/Service-Übersicht, Verwaltung mehrerer Installationen aus einer Admin-UI heraus (Konzept 8).
**Konzept-Referenz:** 8, 3a
**Kein eigenes Postgres-Schema** — reine clientseitig gerenderte SPA (statischer Export, siehe [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), kein eigener Backend-Prozess.

## Ort im Repo

`apps/admin-ui/` — identisches Muster wie `apps/user-ui` (nicht unter `services/`, siehe ADR 0006): Next.js/TypeScript, `output: "export"`, Auslieferung über `nginx`, kein Node-Prozess zur Laufzeit.

## Seiten

| Route | Zweck |
|---|---|
| `/login/` | Anmeldung (identischer Ablauf wie User-UI, gegen den Auth Service der **aktiven Installation** über deren Gateway) |
| `/` | Startseite mit Hinweistext, Navigation läuft über die Seitenleiste (`AdminShell`) |
| `/users/` | Nutzer anlegen/löschen, Rollen anlegen, Rollenzuweisungen anlegen/entfernen |
| `/object-types/` | Objekttypen anlegen/löschen (Attribute als JSON-Liste, siehe Constraint Engine) |
| `/registry/` | Alle bei der Registry registrierten Instanzen inkl. Health-Status |
| `/installations/` | Installationsliste verwalten (anlegen/löschen/wechseln) — seit P4-S5 |

Alle Seiten außer `/login/` sind über `RequireAuth` geschützt (clientseitiger Redirect, kein Server für Middleware verfügbar — wie bei der User-UI). `RequireAuth` prüft die Sitzung der **aktiven Installation**.

## Layout (P4-S5, Nutzer-Feedback nach dem ersten echten Browser-Test des MVP)

Ersetzt die frühere flache Top-Nav-Leiste durch ein klassisches Management-Dashboard-Layout (Konzept 8):

- **`AdminSidebar`** (links): gruppierte, einzeln ausklapp-/einklappbare Navigation (`sidebar-group`-Blöcke, Auf-/Zuklapp-Zustand pro Browser in `localStorage` gemerkt). Aktuell zwei Gruppen — "Verwaltung" (Nutzer & Rollen, Objekttypen, Registry) und "Installationen" — generisch gebaut für weitere Gruppen in künftigen Sessions.
- **`AdminShell`**: Kopfzeile (Titel, `InstallationSwitcher`, `ThemeSwitcher` seit P4-S6, Nutzername, Abmelden) + Hauptbereich rechts, der die jeweils gewählte Seite zeigt.

## Mehrfachinstallationen (Konzept 3a/8, seit P4-S5)

Die Admin-UI kann mehrere vollständig unabhängige DMS-Installationen verwalten, ohne sich bei jedem Wechsel neu anmelden zu müssen — siehe [ADR 0008](../adr/0008-admin-ui-multi-installation-sessions.md) für die technische Begründung. Kurzfassung:

- Installationsliste (`{id, name, gatewayBaseUrl}`) rein clientseitig in `localStorage`, verwaltet über `InstallationManager` (`/installations/`) und `useInstallation()` (`lib/installation-context.tsx`).
- `InstallationSwitcher` in der Kopfzeile wechselt die aktive Installation — bleibt ausgeblendet, solange nur eine Installation konfiguriert ist.
- **Eigene Sitzung je Installation**: `auth-context.tsx` speichert Tokens unter `dms.tokens.<installationId>` statt eines einzigen globalen Schlüssels. Ein Wechsel zu einer bereits einmal angemeldeten Installation erfordert keine erneute Anmeldung, solange deren Sitzung noch gültig ist; eine neue, noch nie angemeldete Installation zeigt beim Wechsel den Login.
- **Kein Single-Sign-on über Installationsgrenzen hinweg** — bewusst, entspricht der vollständigen Isolation aus Konzept 3a.
- `lib/api.ts`s Gateway-Adresse ist seit dieser Session eine mutable Modulvariable (`setGatewayBaseUrl()`) statt einer festen Konstante, vom `InstallationProvider` bei jedem Wechsel synchron gesetzt.

## Theming (Konzept 8, seit P4-S6)

`src/lib/theme-context.tsx` (`ThemeProvider`/`useTheme()`) — identisches Muster wie in der User-UI (bewusst dupliziert statt geteilt, ADR 0006), umschaltbar über den `ThemeSwitcher` in der Kopfzeile. Geräteübergreifend am Nutzerkonto der **aktiven Installation** gespeichert (`GET/PUT /api/auth-service/me/preferences`, `accessToken` kommt aus dem installationsbezogenen `AuthProvider`, ADR 0008), siehe [ADR 0009](../adr/0009-cross-ui-theming-profile-persistence.md). Der `localStorage`-Cache-Schlüssel (`dms.theme`) ist bewusst **nicht** installationsspezifisch — ein Installationswechsel zeigt kurzzeitig noch die zuletzt gecachte Theme-Wahl, bis die neue Installation ihre eigene Präferenz nachgeladen hat (siehe ADR 0009 "Konsequenzen").

## Anbindung an das Backend

Ausschließlich über das API-Gateway der jeweils **aktiven Installation** (3.5, `/api/{service_type}/{path}`):

| Bereich | Gateway-Aufrufe |
|---|---|
| Login/Identität | `POST /api/auth-service/login`, `GET /api/auth-service/me` |
| Nutzer | `GET/POST /api/auth-service/users`, `DELETE /api/auth-service/users/{id}` |
| Rollen | `GET/POST /api/permission-service/roles` |
| Rollenzuweisungen | `GET/POST /api/permission-service/role-assignments`, `DELETE .../{id}` |
| Objekttypen | `GET/POST/DELETE /api/object-type-service/object-types` |
| Registry | `GET /api/registry-service/instances` |
| Theme-Präferenz | `GET/PUT /api/auth-service/me/preferences` (seit P4-S6) |

## Auth-Zustand

`src/lib/auth-context.tsx` — installationsbezogen seit P4-S5 (siehe oben), sonst wie die User-UI: `localStorage`-Tokens, proaktiver Refresh — bewusst dupliziert statt geteilt (ADR 0006: keine gemeinsame Fachlogik zwischen unabhängig deploybaren Frontend-Apps).

## Internationalisierung (Konzept 8, seit P4-S3)

Wie die User-UI: `src/i18n/de.json` + `useI18n()` (siehe [ADR 0007](../adr/0007-frontend-i18n-preparation.md)). Eigenes Wörterbuch, da Admin-UI-Begriffe (Nutzerverwaltung, Objekttyp-Editor, Registry, Installationen) sich vollständig von den User-UI-Begriffen unterscheiden.

## Autorisierung — bewusst noch nicht durchgesetzt

Wie bei allen bisherigen "Admin"-Endpunkten dieses Projekts (Force-Unlock, Bereichssperren, Nutzer-/Objekttyp-Verwaltung): Das Gateway prüft nur, dass ein gültiger Bearer-Token vorliegt, nicht, ob der Principal zu der jeweiligen administrativen Aktion berechtigt ist. **Jeder erfolgreich angemeldete Nutzer kann aktuell die Admin-UI vollständig nutzen** — es gibt keine Rollenprüfung. Reale Autorisierung bräuchte eine Auswertung der vom Gateway weitergereichten Identitäts-Header (oder einer äquivalenten serverseitigen Prüfung) in den jeweiligen Backend-Services selbst.

## Build & Auslieferung

Zweistufiges Docker-Image (`apps/admin-ui/Dockerfile`), identisch zur User-UI. `NEXT_PUBLIC_GATEWAY_BASE_URL` als Build-Arg (Startwert der "Lokal"-Installation), überschreibbar über `ADMIN_UI_GATEWAY_BASE_URL` in `infra/.env`. Port `3001` (User-UI: `3000`). Weitere Installationen werden zur Laufzeit über `/installations/` hinzugefügt, nicht über einen erneuten Build.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build`.
- `npm test` (Vitest + Testing Library, **36 Tests**): API-Client (inkl. Routing über die aktiv gesetzte Gateway-Adresse), `AuthProvider` (Login/Logout/Session-Wiederherstellung, seit P4-S5 zusätzlich: Sitzungsisolation zwischen zwei Installationen, kein erneutes Login beim Zurückwechseln), `InstallationProvider` (Bootstrap, Hinzufügen/Wechseln/Entfernen, Schutz vor Entfernen der letzten Installation, Persistenz), `ThemeProvider` (seit P4-S6: Default `auto`, `data-theme`-Attribut, `localStorage`-Cache-Wiederherstellung, `setTheme`-Persistenz), `AdminSidebar` (Gruppen auf-/zuklappen inkl. Persistenz), `InstallationManager`/`InstallationSwitcher`, `UserManagement`, `ObjectTypeEditor`, `RegistryOverview` — Netzwerkschicht gemockt, gleiche Begründung wie bei der User-UI. `matchMedia` wird in `tests/setup.ts` gepolyfillt, da jsdom es nicht implementiert.
- **Kein Browser in dieser Entwicklungsumgebung verfügbar** (siehe `docs/services/user-ui.md`) — jeder Gateway-Aufruf der Admin-UI wurde einzeln per `curl` gegen den echten laufenden Compose-Stack nachvollzogen (seit P4-S6 zusätzlich: `GET/PUT /me/preferences` inkl. 422 bei ungültigem Theme-Wert). Das Multi-Installation-Verhalten und das Theme-Umschalten selbst (Umschalten per Dropdown, Sidebar-Auf-/Zuklappen, Flash-freier Themewechsel) sind rein clientseitig und wurden **nur über die Vitest-Komponententests verifiziert, nicht visuell im Browser** — an den Nutzer explizit als Einschränkung kommuniziert.

## Offene Punkte

- **Keine Autorisierung** (s. o.) — nach wie vor der wichtigste offene Punkt.
- Objekttyp-Attribute werden als rohes JSON eingegeben (kein visueller Attribut-Editor) — bewusste Vereinfachung für das Grundgerüst.
- Keine Bearbeitung bestehender Objekttypen (`PUT /object-types/{id}` existiert im Backend, aber kein UI-Formular dafür) — nur Anlegen/Löschen.
- Keine Gruppen-Verwaltung, nur einzelne Nutzer (Permission Service unterstützt `principal_type=group` bereits, UI bietet nur `user` an).
- Workflow-Designer, Lizenzübersicht, Audit-Trail-Ansicht, Konfigurationsim-/export (Konzept 8 nennt sie für die Admin-UI) sind nicht Teil dieses Grundgerüsts — die zugrundeliegenden Services existieren noch nicht.
- i18n nur strukturell vorbereitet (ADR 0007), keine zweite Sprache und keine UI-Sprachumschaltung.
- Installationsliste ist rein lokal im Browser gespeichert, kein geräteübergreifendes Provisioning (siehe ADR 0008 "Konsequenzen") — das wäre Aufgabe des optionalen, noch nicht gebauten Fleet-/Lizenz-Management-Service (Konzept 3a, Phase 13).
- Theme-Präferenz hat keinen Konfliktauflösungsmechanismus zwischen Geräten/Installationen (letzter Fetch gewinnt) und kein Retry bei fehlgeschlagenem `PUT /me/preferences` (siehe ADR 0009 "Konsequenzen").
