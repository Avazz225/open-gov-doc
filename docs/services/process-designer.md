# process-designer

**Verantwortung:** Eigenständige Frontend-Anwendung für grafische BPMN-2.0-Modellierung gegen die Workflow Engine (`workflow-service`, P6-S1) — Prozessdefinitionen anlegen/öffnen/bearbeiten, BPMN-XML importieren/exportieren, Signature Task (3.10) über ein eigenes Properties-Panel konfigurieren, Prozess-Versionierung (7.1/8, P6-S8), seit **P6-S9** föderierte Prozessschritte (7.4) über ein eigenes Properties-Panel konfigurieren.
**Konzept-Referenz:** 7.1, 8, 3.10, 7.4
**Kein eigenes Postgres-Schema** — reine clientseitig gerenderte SPA (statischer Export, gleiches Muster wie `apps/user-ui`, siehe [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), kein eigener Backend-Prozess.

## Ort im Repo

`apps/process-designer/` — bewusst **nicht** unter `services/` (Node/React-Toolchain statt Python-Service-Template, ADR 0006) und bewusst **nicht** Teil der Admin-UI (wörtliche Konzept-Vorgabe 7.1/8: "eigenständige Frontend-Anwendung").

## Single-Installation (bewusst kein Multi-Installation wie Admin-UI)

Anders als `apps/admin-ui` (`InstallationProvider`, Multi-Installation, ADR 0008) folgt der Process Designer dem einfacheren `apps/user-ui`-Muster: eine einzelne, zur Build-Zeit fest eingebrannte Gateway-Adresse (`NEXT_PUBLIC_GATEWAY_BASE_URL`), globaler `dms.tokens`-Storage-Key. **Explizite Nutzerentscheidung bei der Planfreigabe**: der Process Designer bleibt keine Multi-Installation, auch nachdem installationsübergreifende Funktionalität (externe Swimlanes/Handover, siehe unten) gefordert wurde — wer die UI nutzen will, betreibt den Container in der eigenen Installation, ein Wechsel zwischen mehreren Installationen (wie in der Admin-UI) ist nicht vorgesehen.

## Seiten

| Route | Zweck |
|---|---|
| `/login/` | Anmeldung (identisch zu user-ui/admin-ui) |
| `/` | `ProcessDefinitionList` — nur erreichbar mit gültiger Session |
| `/designer/` | BPMN-Canvas + Properties Panel, optionaler `?id=`-Query-Parameter (kein dynamischer Next-Routen-Parameter, gleiches SPA-State-Muster wie die bestehenden Apps) |

`RequireAuth` rendert wie in user-ui/admin-ui zuerst ein `MaintenanceBanner` (Not-Shutdown, 4.8).

## Autorisierung (button-, nicht routenbezogen)

**Lesen/Öffnen/Canvas-Anzeige bleibt für jeden authentifizierten Principal offen** (`GET /process-definitions*` ist bei `workflow-service` ungegated). Nur **Speichern/Löschen** sind an die seit P6-S6 bestehende Capability `admin.object_config` gebunden — geprüft über `permissions.includes("admin.object_config")` direkt in `ProcessDefinitionList`/`designer/page.tsx` (Button ausgeblendet/deaktiviert, kein routenweites `RequireCapability`-Redirect wie in der Admin-UI, da Lesen weiterhin erlaubt bleiben soll). Der Backend-Endpunkt selbst setzt dieselbe Regel ohnehin durch (`403`) — das UI-Gating ist reine UX-Vorwegnahme.

## Prozess-Versionierung (seit P6-S8, [ADR 0027](../adr/0027-workflow-process-definition-versioning.md))

`name` ist der Prozessfamilien-Schlüssel im Backend, nicht mehr global eindeutig. `ProcessDefinitionList` zeigt standardmäßig nur die **neueste Version je Familie** (`GET /process-definitions`), eine aufklappbare Versionshistorie je Zeile lädt bei Bedarf `GET /process-definitions?name=X` nach (vollständige Historie, neueste zuerst). "Speichern" im Designer hat keinen separaten "Version vs. neue Familie"-Schalter: das Namensfeld entscheidet — unverändert lassen speichert eine neue Version derselben Familie, ein geänderter Name legt eine neue Familie (Version 1) an. Nach dem Speichern navigiert die Seite zur neu erzeugten `id` (`router.replace`), die Erfolgsmeldung nennt die vergebene Versionsnummer.

## BPMN-Canvas (`components/BpmnDesigner.tsx`)

`bpmn-js` (**ohne** `bpmn-js-spiffworkflow`, siehe [ADR 0026](../adr/0026-process-designer-bpmn-js-without-spiffworkflow-addon.md)) + `bpmn-js-properties-panel` + `@bpmn-io/properties-panel` + `camunda-bpmn-moddle` (für die von `workflow-service`s `CamundaParser`, P6-S7, gelesenen `camunda:`-Erweiterungselemente). Manuelles `useRef`/`useEffect`-Mounting statt eines React-Wrappers (kein aktiv gepflegter React-Wrapper für bpmn-js verfügbar), eingebunden über `next/dynamic`/`{ssr:false}` in `designer/page.tsx` (bpmn-js manipuliert das DOM direkt — unvereinbar mit Next.js' Build-Zeit-Renderdurchlauf unter `output:"export"`). Der Modeler exponiert `exportXml`/`importXml` über ein `onReady(handle)`-Callback-Prop statt `forwardRef`/`useImperativeHandle` — vermeidet jede Unsicherheit über Ref-Weiterreichung durch `next/dynamic`.

Standard-Tasktypen (Manual/Script Task) nutzen bpmn-js' eingebautes Standard-Palette-/Kontext-Pad-Verhalten, kein eigener Palette-Eintrag.

## Signature Task Properties Panel (3.10, `components/SignatureTaskPropertiesProvider.tsx`)

Der Signature Task ist **kein eigenes BPMN-Element**, sondern ein `bpmn:ManualTask` mit einer zusätzlichen, eigenen Properties-Panel-Gruppe (sichtbar nur bei Manual Tasks): Checkbox "Signatur erforderlich" + Niveau-Auswahl (SES/AES/QES), liest/schreibt `bpmn:extensionElements/camunda:properties` (`taskType=signature`, `requiredLevel=...`) — exakt das Format, das `workflow-service`s `CamundaParser` seit P6-S7 als Signature Task erkennt. Provider-/Group-/Entry-Registrierungsmuster (`propertiesPanel.registerProvider`, `bpmnFactory.create`, `commandStack.execute('element.updateModdleProperties', ...)`) gegen den tatsächlich installierten, gebündelten Quelltext von `bpmn-js-properties-panel` nachvollzogen, nicht angenommen (siehe ADR 0026). `useService` wird von `bpmn-js-properties-panel` reexportiert, nicht von `@bpmn-io/properties-panel` — beim ersten produktiven Build als Import-Fehler aufgefallen und korrigiert.

## Föderierter Schritt Properties Panel (7.4, `components/FederatedStepPropertiesProvider.tsx`, seit P6-S9)

Gleiches Grundmuster wie der Signature Task: **kein eigenes BPMN-Element**, sondern eine zusätzliche Properties-Panel-Gruppe "Föderation (7.4)" auf `bpmn:ManualTask`-Elementen — Checkbox "Föderierter Schritt", Dropdown "Zielinstallation", Textfeld "Ziel-Prozesstyp", liest/schreibt `camunda:properties` (`taskType=federated`, `targetInstallationId`, `targetProcessType`) — exakt das Format, das `workflow-service`s `_dispatch_pending_federation_tasks` seit P6-S9 als föderierten Schritt erkennt (siehe `docs/services/workflow-service.md` "Federation"). Bewusst dupliziertes statt geteiltes Hilfsmodul mit `SignatureTaskPropertiesProvider.tsx` — zwei unabhängige, kleine Provider sind einfacher nachvollziehbar als eine vorzeitig geteilte Abstraktion für zwei Anwendungsfälle.

Die Installationsliste für das Dropdown wird **einmalig vor dem Erzeugen des Modelers** geladen (`designer/page.tsx`, `listFederationInstallations()`) und als statischer didi-Wert in `additionalModules` injiziert (`{ federationInstallations: ["value", ...] }`) — kein Live-Nachladen während einer Bearbeitungssitzung. **Die gesamte Gruppe bleibt ausgeblendet, wenn die Liste leer ist** (kein Hub konfiguriert oder keine Installationen im Adressbuch bekannt) — erfüllt Konzept 7.1 wörtlich: "bietet der Process Designer föderierte Prozessschritte gar nicht erst als Auswahlmöglichkeit an". Bewusst **keine echte Swimlane-Bearbeitung** in bpmn-js (kein etabliertes Provider-Muster dafür, deutlich höheres Risiko) — die Roadmap-Formulierung "externe Swimlanes" ist hier rein als UX-Rahmung zu verstehen, technisch bleibt das Ziel eine Eigenschaft des einzelnen Prozessschritts, nicht der Lane.

## Anbindung an das Backend

Ausschließlich über das API-Gateway (3.5), keine direkten Aufrufe:

| Aktion | Gateway-Aufruf |
|---|---|
| Anmelden | `POST /api/auth-service/login` |
| Identität nach Login | `GET /api/auth-service/me` |
| Effektive Capabilities (4.6) | `GET /api/permission-service/effective-permissions/{sub}/root` |
| Neueste Version je Prozessfamilie | `GET /api/workflow-service/process-definitions` |
| Vollständige Versionshistorie einer Familie | `GET /api/workflow-service/process-definitions?name=X` |
| Einzelne Prozessdefinition inkl. BPMN-XML | `GET /api/workflow-service/process-definitions/{id}` |
| Speichern (neue Version oder neue Familie) | `POST /api/workflow-service/process-definitions` (multipart, `admin.object_config`) |
| Löschen einer Version | `DELETE /api/workflow-service/process-definitions/{id}` (`admin.object_config`, `409` bei aktiven Instanzen) |
| Theme-Präferenz lesen/schreiben | `GET/PUT /api/auth-service/me/preferences` |
| Not-Shutdown / Wartungsmodus-Status | `GET /api/permission-service/maintenance-mode` |
| Federation-Hub-Adressbuch (seit P6-S9) | `GET /api/workflow-service/federation/installations` (Proxy, ungegated, leer ohne konfigurierten Hub) |

## Theming/i18n/Auth-Zustand

Identische Provider-Kopie aus user-ui/admin-ui (`ThemeProvider`, `I18nProvider`, `auth-context.tsx`), eigenes `src/i18n/de.json`. `auth-context.tsx` ist eine Hybridvariante: globaler `dms.tokens`-Key wie user-ui, aber zusätzlich ein `permissions: string[]`-Feld (`getEffectivePermissions`) wie admin-ui, für das Capability-Gating oben.

## Build & Auslieferung

Zweistufiges Docker-Image (`apps/process-designer/Dockerfile`, `node:22-alpine` Build-Stage → `nginx:alpine` Laufzeit), `NEXT_PUBLIC_GATEWAY_BASE_URL` als Build-Arg, überschreibbar über `PROCESS_DESIGNER_GATEWAY_BASE_URL` in `infra/.env`. `infra/docker-compose.yml`: Port `${PROCESS_DESIGNER_PORT:-3002}:80`.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build` — TypeScript-Prüfung, ESLint (inkl. einer bewussten, begründeten `no-explicit-any`-Ausnahme in `SignatureTaskPropertiesProvider.tsx`: `bpmn-js` selbst typisiert `Moddle`/`ModdleElement` als `any`, die beiden Properties-Panel-Pakete liefern gar keine Typdeklarationen), produktionsfähiger statischer Export.
- `npm test` (Vitest + Testing Library, **28 Tests**): `AuthProvider` (Login/Logout/Session-Wiederherstellung inkl. `permissions`), `ThemeProvider` (Default/Cache/Persistenz, Kopie des user-ui-Musters), `ProcessDefinitionList` (zeigt nur die neueste Version, Versionshistorie auf-/zuklappbar, Löschen nur mit `admin.object_config` inkl. Hinweistext ohne, Backend-Fehlermeldung bei `409`), `BpmnDesigner` (mit gemocktem `bpmn-js`: Modeler wird seit P6-S9 mit sechs statt vier erwarteten Modulen instanziiert, `initialXml` wird importiert, `onReady`/`onImportError`-Callbacks, Zerstören beim Unmount), `SignatureTaskPropertiesProvider` (reine `getSignatureLevel`/`isSignatureRequired`-Lesefunktionen gegen ein minimales moddle-Element-Double, ohne echtes `bpmn-moddle`-Modell oder DOM), seit **P6-S9** `FederatedStepPropertiesProvider` (analoge reine Lesefunktionen `isFederatedStepEnabled`/`getTargetInstallationId`/`getTargetProcessType`).
- **Kein Browser in dieser Entwicklungsumgebung** (wie jede vorherige UI-Session) — Canvas-Interaktionen (Drag & Drop, Kontext-Pad, Properties-Panel-Bedienung) konnten nicht visuell verifiziert werden, nur über die genannten Vitest-Tests plus einen curl-bestätigten Datenrundlauf gegen den echten `workflow-service` (siehe `PROGRESS.md`, Smoke-Test-Protokoll dieser Session).

## Offene Punkte

- **Keine Validierung referenzierter Objekttypen/Ordnerziele beim Import** — kein aktuell existierender Task-Typ in diesem System hält solche Referenzen (siehe `docs/services/workflow-service.md` "Offene Punkte"). Import-Validierung beschränkt sich auf clientseitiges `importXML()`-Fehlschlagen und die bereits bestehende `workflow-service`-Serverseitige Prüfung (`422` bei nicht parsbarer BPMN).
- **Federation Hub / föderierte Prozessschritte seit P6-S9 umgesetzt** (siehe "Föderierter Schritt Properties Panel" oben, `docs/services/federation-hub-service.md`) — bewusst **keine echte Swimlane-Bearbeitung** (Zielinstallation ist eine Eigenschaft des Prozessschritts, nicht einer Lane), keine Validierung, ob der eingetragene `targetProcessType` auf der Zielinstallation tatsächlich existiert (das erfährt der Designer nicht — nur das Hub-Adressbuch, nicht die Prozesskataloge der Zielinstallationen).
- **Kein Anschluss an Konfigurationsexport/-import** (7.3) — Prozessdefinitionen sind aktuell nicht Teil eines geräteübergreifenden Konfigurationsexports, ein möglicher späterer Ausbau.
- **Kein Rollback-Endpunkt/keine Familien-Löschung** (siehe ADR 0027 "Konsequenzen") — eine ältere Version lässt sich öffnen/exportieren, aber nicht direkt "als neueste wiederherstellen"; Löschen bleibt pro Version.
- **Keine Race-Condition-Sperre bei der Versionsvergabe** (siehe ADR 0027) — für ein Grundgerüst ohne hochfrequente parallele Speicherungen akzeptiert.
- Kein automatisiertes Browser-E2E in dieser Umgebung möglich (kein Chrome/Chromium installiert) — nachzuholen, sobald eine Umgebung mit Browser verfügbar ist.
