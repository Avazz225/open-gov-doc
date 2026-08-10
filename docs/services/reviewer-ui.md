# reviewer-ui

**Verantwortung:** Eigenständige Frontend-Anwendung mit schlankem Fokus nur auf Freigabeaufgaben (8, wörtlich: "dedizierte Reviewer/Approval-UI (schlanker Fokus nur auf Freigabeaufgaben, auch für Vier-Augen-Fälle)"), P14-S2. Zwei Bereiche: eine Cross-Instanz-Aufgabenliste für bereite BPMN-Manual-/Signature-Tasks (`workflow-service`) und eine generische Vier-Augen-Freigabe-Inbox über alle Aktionstypen (`permission-service`, 4.3).
**Konzept-Referenz:** 8, 7.1, 4.3, 3.10
**Kein eigenes Postgres-Schema** — reine clientseitig gerenderte SPA (statischer Export, gleiches Muster wie `apps/user-ui`, siehe [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), kein eigener Backend-Prozess.
**ADR:** [0041 — Scope + neue Cross-Instanz-Aufgabenliste, keine neue Autorisierungsschicht](../adr/0041-reviewer-ui-migration-console-scope-and-cross-instance-tasks.md)

## Ort im Repo

`apps/reviewer-ui/` — bewusst **nicht** unter `services/` (Node/React-Toolchain statt Python-Service-Template, ADR 0006) und bewusst **nicht** Teil der Admin-UI (wörtliche Konzept-Vorgabe: "eigenständige ... UI").

## Seiten

| Route | Zweck |
|---|---|
| `/login/` | Anmeldung (identisch zu user-ui/admin-ui/process-designer) |
| `/` | `TaskList` — Aufgaben-Inbox, nur erreichbar mit gültiger Session |
| `/approvals/` | `ApprovalList` — Vier-Augen-Freigabe-Inbox |

`RequireAuth` rendert eine gemeinsame `Shell` (Kopfzeile mit Tab-Navigation zwischen beiden Bereichen, Theme-Umschalter, Logout) sowie zuvor ein `MaintenanceBanner` (Not-Shutdown, 4.8) — bewusst eine einfache zweigliedrige Tab-Leiste statt einer vollen Seitennavigation wie `AdminShell` (admin-ui), da diese App nur zwei Bereiche hat.

## Aufgaben-Inbox (7.1, `components/TaskList.tsx`)

Konsumiert den neuen `GET /tasks`-Endpunkt (`workflow-service`, P14-S2) — die erste Cross-Instanz-Aufgabenliste im gesamten System (vorher gab es nur `GET /instances/{id}/tasks`, das eine bereits bekannte Instanz-ID voraussetzt). Zeigt je Aufgabe Name, zugehörige Prozessdefinition, Bezugsobjekt (`business_key`), Bahn (`lane`) sowie einen "Bearbeiten"-Button, der ein Inline-Formular öffnet:

- **Gewöhnliche Manual Tasks**: nur `completed_by` (vorbelegt mit dem angemeldeten Nutzernamen) + optionale zusätzliche Prozessdaten als JSON-Freitext.
- **Signature Tasks** (3.10, erkannt an `extensions.taskType === "signature"`, sichtbar an einem eigenen Badge): zusätzlich ein Pflichtfeld für die `signature_id` — muss auf eine beim `signature-service` bereits existierende, zum Task-Dokument passende Signatur mit ausreichendem Niveau verweisen, sonst lehnt `workflow-service` mit `400` ab (unverändertes Backend-Verhalten, siehe `docs/services/workflow-service.md` "Signature Task").

Föderierte Tasks (`taskType=federated`/`federated_return`, 7.4) tauchen in der Liste gar nicht erst auf — `GET /tasks` filtert sie bereits serverseitig heraus, da sie ausschließlich automatisch über den Federation Hub abgeschlossen werden (ein direkter Abschlussversuch würde ohnehin `409` liefern).

## Freigaben-Inbox (4.3, `components/ApprovalList.tsx`)

Konsumiert `permission-service`s `GET /approval-requests` **ungefiltert nach Aktionstyp** — die erste generische UI-Oberfläche für diese API im gesamten System (bislang nur drei eng gefilterte Einzelkonsumenten, siehe ADR 0041 "Begründung"). Status-Filter (offen/freigegeben/abgelehnt/alle, Default "offen"), Detailansicht zeigt das rohe `payload`-JSON der Anfrage (die UI kennt die Fachbedeutung einzelner `action_type`s bewusst nicht). Freigeben/Ablehnen ruft `POST .../approve`/`.../reject` mit dem angemeldeten Nutzernamen als `approved_by`/`rejected_by` — serverseitig weiterhin durchgesetzt, dass Initiator und Entscheider nicht identisch sein dürfen (Kern-Vier-Augen-Regel, `403` sonst).

## Autorisierung

**Keine capability-gegateten Aktionen in dieser App** — weder Task-Abschluss noch Freigabe-Entscheidungen sind backend-seitig an eine domänengetrennte Admin-Rolle gebunden (siehe ADR 0041 "Begründung"). `RequireAuth` prüft nur, ob überhaupt eine gültige Sitzung vorliegt, kein `RequireCapability`-Redirect wie in der Admin-UI. `getEffectivePermissions` wird trotzdem abgerufen (identisches Muster wie die übrigen Apps), aktuell aber an keiner Stelle ausgewertet — Vorbereitung für eine mögliche spätere, gezieltere Einschränkung.

## Anbindung an das Backend

Ausschließlich über das API-Gateway (3.5):

| Aktion | Gateway-Aufruf |
|---|---|
| Anmelden | `POST /api/auth-service/login` |
| Identität nach Login | `GET /api/auth-service/me` |
| Bereite Aufgaben über alle laufenden Instanzen (neu, P14-S2) | `GET /api/workflow-service/tasks` |
| Aufgabe abschließen | `POST /api/workflow-service/instances/{instance_id}/tasks/{task_id}/complete` |
| Freigabeanfragen listen | `GET /api/permission-service/approval-requests?status=` |
| Freigeben/Ablehnen | `POST /api/permission-service/approval-requests/{id}/approve\|reject` |
| Theme-Präferenz lesen/schreiben | `GET/PUT /api/auth-service/me/preferences` |
| Not-Shutdown / Wartungsmodus-Status | `GET /api/permission-service/maintenance-mode` |

## Theming/i18n/Auth-Zustand

Identische Provider-Kopie aus user-ui/admin-ui/process-designer (`ThemeProvider`, `I18nProvider`, `auth-context.tsx`), eigenes `src/i18n/de.json`, globaler `dms.tokens`-Storage-Key (Single-Installation wie process-designer/user-ui, kein `InstallationSwitcher` wie admin-ui).

## Build & Auslieferung

Zweistufiges Docker-Image (`apps/reviewer-ui/Dockerfile`, `node:22-alpine` Build-Stage → `nginx:alpine` Laufzeit), `NEXT_PUBLIC_GATEWAY_BASE_URL` als Build-Arg, überschreibbar über `REVIEWER_UI_GATEWAY_BASE_URL` in `infra/.env`. `infra/docker-compose.yml`: Port `${REVIEWER_UI_PORT:-3005}:80` — **nicht** 3003 (durch `GRAFANA_PORT`, 10.1, bereits belegt, siehe ADR 0041).

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build` — TypeScript-Prüfung, ESLint, produktionsfähiger statischer Export.
- `npm test` (Vitest + Testing Library, **15 Tests**): `AuthProvider` (Login/Logout/Session-Wiederherstellung, 4 Tests), `TaskList` (leere Liste, Auflistung mit Prozess-/Bezugsobjekt-Kontext, Signatur-Badge + Pflichtfeld, erfolgreicher Abschluss inkl. Neuladen, Ablehnung bei ungültigem JSON in den Zusatzdaten, 5 Tests), `ApprovalList` (Default-Filter "offen", leere Liste, Detail-Payload aufklappen, Freigeben als angemeldeter Nutzer, Ablehnen mit optionaler Begründung, keine Aktionen bei bereits entschiedener Anfrage, 6 Tests).
- Live gegen den gebauten Container in einem echten (headless) Browser verifiziert (Login, Aufgabenliste inkl. Bearbeiten-Formular, Freigaben-Tab inkl. Statusfilter-Wechsel, Design-Umschalter auf Dunkel — jeweils ohne Konsolenfehler; die Aufgabenliste zeigte dabei reale, aus früheren Testläufen liegen gebliebene Tasks, ein zusätzlicher Beleg, dass `GET /tasks` echte Daten korrekt aggregiert).

## Offene Punkte

- **Kein Server-Push/keine Benachrichtigung** — reine Pull-Oberfläche, muss aktiv neu geladen werden (siehe ADR 0041 "Konsequenzen").
- **Keine lane-/rollenbasierte Vorauswahl der Aufgabenliste** ("nur meine Aufgaben") — `workflow-service` setzt BPMN-Lanes bislang an keiner Stelle durch (bereits dokumentierte Grenze, `docs/services/workflow-service.md` "Offene Punkte"), jeder angemeldete Principal sieht dieselbe vollständige Liste.
- **Kein eigenes Ablehnen-Dialogformular** — `window.prompt` für die optionale Begründung, ausreichend für eine Referenzimplementierung.
