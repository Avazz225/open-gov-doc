# admin-ui

**Verantwortung:** Administrative Web-Oberfläche — Nutzer-/Rollenverwaltung, Objekttyp-Editor, Registry-/Service-Übersicht (Konzept 8, Grundgerüst).
**Konzept-Referenz:** 8
**Kein eigenes Postgres-Schema** — reine clientseitig gerenderte SPA (statischer Export, siehe [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), kein eigener Backend-Prozess.

## Ort im Repo

`apps/admin-ui/` — identisches Muster wie `apps/user-ui` (nicht unter `services/`, siehe ADR 0006): Next.js/TypeScript, `output: "export"`, Auslieferung über `nginx`, kein Node-Prozess zur Laufzeit.

## Seiten

| Route | Zweck |
|---|---|
| `/login/` | Anmeldung (identischer Ablauf wie User-UI, gegen den Auth Service über das Gateway) |
| `/` | Startseite mit Navigation zu den drei Bereichen |
| `/users/` | Nutzer anlegen/löschen, Rollen anlegen, Rollenzuweisungen anlegen/entfernen |
| `/object-types/` | Objekttypen anlegen/löschen (Attribute als JSON-Liste, siehe Constraint Engine) |
| `/registry/` | Alle bei der Registry registrierten Instanzen inkl. Health-Status |

Alle vier Seiten außer `/login/` sind über `RequireAuth` geschützt (clientseitiger Redirect, kein Server für Middleware verfügbar — wie bei der User-UI).

## Anbindung an das Backend

Ausschließlich über das API-Gateway (3.5, `/api/{service_type}/{path}`):

| Bereich | Gateway-Aufrufe |
|---|---|
| Login/Identität | `POST /api/auth-service/login`, `GET /api/auth-service/me` |
| Nutzer | `GET/POST /api/auth-service/users`, `DELETE /api/auth-service/users/{id}` (seit P4-S3, siehe `docs/services/auth-service.md`) |
| Rollen | `GET/POST /api/permission-service/roles` |
| Rollenzuweisungen | `GET/POST /api/permission-service/role-assignments`, `DELETE .../{id}` (Listing-Endpunkt seit P4-S3, siehe `docs/services/permission-service.md`) |
| Objekttypen | `GET/POST/DELETE /api/object-type-service/object-types` |
| Registry | `GET /api/registry-service/instances` — funktioniert nur, weil sich die Registry seit dieser Session **bei sich selbst** registriert (siehe `docs/services/registry-service.md`); vorher gab es für `service_type=registry-service` keine auflösbare Instanz. |

## Auth-Zustand

Identisch zur User-UI: `src/lib/auth-context.tsx` mit `localStorage`-Tokens und proaktivem Refresh — bewusst dupliziert statt geteilt (ADR 0006: keine gemeinsame Fachlogik zwischen unabhängig deploybaren Frontend-Apps).

## Internationalisierung (Konzept 8, seit P4-S3)

Wie die User-UI: `src/i18n/de.json` + `useI18n()` (siehe [ADR 0007](../adr/0007-frontend-i18n-preparation.md)). Eigenes Wörterbuch, da Admin-UI-Begriffe (Nutzerverwaltung, Objekttyp-Editor, Registry) sich vollständig von den User-UI-Begriffen unterscheiden.

## Autorisierung — bewusst noch nicht durchgesetzt

Wie bei allen bisherigen "Admin"-Endpunkten dieses Projekts (Force-Unlock, Bereichssperren, jetzt auch Nutzer-/Objekttyp-Verwaltung): Das Gateway prüft nur, dass ein gültiger Bearer-Token vorliegt, nicht, ob der Principal zu der jeweiligen administrativen Aktion berechtigt ist. **Jeder erfolgreich angemeldete Nutzer kann aktuell die Admin-UI vollständig nutzen** — es gibt keine Rollenprüfung, die den Zugriff auf `/users/`, `/object-types/` oder `/registry/` auf bestimmte Konten beschränkt. Reale Autorisierung bräuchte eine Auswertung der vom Gateway weitergereichten Identitäts-Header (oder einer äquivalenten serverseitigen Prüfung) in den jeweiligen Backend-Services selbst.

## Build & Auslieferung

Zweistufiges Docker-Image (`apps/admin-ui/Dockerfile`), identisch zur User-UI. `NEXT_PUBLIC_GATEWAY_BASE_URL` als Build-Arg, überschreibbar über `ADMIN_UI_GATEWAY_BASE_URL` in `infra/.env`. Port `3001` (User-UI: `3000`).

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build`.
- `npm test` (Vitest + Testing Library): API-Client, `AuthProvider` (schlankere Variante als User-UI, gleiche Kernfälle), `UserManagement`, `ObjectTypeEditor`, `RegistryOverview` — Netzwerkschicht gemockt, gleiche Begründung wie bei der User-UI.
- **Kein Browser in dieser Entwicklungsumgebung verfügbar** (siehe `docs/services/user-ui.md`) — stattdessen wurde jeder Gateway-Aufruf der Admin-UI einzeln per `curl` gegen den echten laufenden Compose-Stack nachvollzogen: Login → Nutzer anlegen → Rolle anlegen → Rollenzuweisung anlegen → in der Liste sichtbar → Zuweisung entfernen → Nutzer löschen → Objekttyp anlegen → in der Liste sichtbar → löschen → Registry-Übersicht zeigt alle acht aktuell laufenden, gesunden Backend-Services (inkl. der Registry selbst). Ein Mensch sollte auch diese Oberfläche vor Produktivnutzung im echten Browser durchklicken.

## Offene Punkte

- **Keine Autorisierung** (s. o.) — der wichtigste offene Punkt dieser Session, da die Admin-UI potenziell gefährliche Aktionen (Nutzer löschen, Objekttypen löschen) ungated anbietet.
- Objekttyp-Attribute werden als rohes JSON eingegeben (kein visueller Attribut-Editor) — bewusste Vereinfachung für das Grundgerüst.
- Keine Bearbeitung bestehender Objekttypen (`PUT /object-types/{id}` existiert im Backend, aber kein UI-Formular dafür) — nur Anlegen/Löschen.
- Keine Gruppen-Verwaltung, nur einzelne Nutzer (Permission Service unterstützt `principal_type=group` bereits, UI bietet nur `user` an).
- Workflow-Designer, Lizenzübersicht, Audit-Trail-Ansicht, Konfigurationsim-/export (Konzept 8 nennt sie für die Admin-UI) sind nicht Teil dieses Grundgerüsts — die zugrundeliegenden Services (Workflow Engine, License Service) existieren noch nicht.
- i18n nur strukturell vorbereitet (ADR 0007), keine zweite Sprache und keine UI-Sprachumschaltung.
