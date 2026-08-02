# process-designer

Eigenständige Frontend-Anwendung für die grafische BPMN-2.0-Modellierung
(Konzept 7.1/8) — **nicht** Teil der Admin-UI. Modelliert Prozessdefinitionen
über [`bpmn-js`](https://bpmn.io) gegen die Workflow Engine aus
`workflow-service` (P6-S1), inkl. eines eigenen Properties-Panel-Providers für
den Signature Task (3.10, P6-S7). Reines Client-Side-Rendering, kein
Node-Prozess in Produktion (identisches Muster wie `apps/user-ui`, siehe
[ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)).

Ausführliche Doku: [`docs/services/process-designer.md`](../../docs/services/process-designer.md).
Bibliotheksentscheidung (kein `bpmn-js-spiffworkflow`): [ADR 0026](../../docs/adr/0026-process-designer-bpmn-js-without-spiffworkflow-addon.md).

## Lokale Entwicklung

```bash
npm install
npm run dev
```

Erwartet ein laufendes Gateway auf `http://localhost:8009`:

```bash
cd ../../infra && docker compose up -d
```

## Build (statischer Export)

```bash
npm run build
```

## Tests

```bash
npm run typecheck
npm run lint
npm test
```

## Docker

```bash
cd ../../infra && docker compose up -d --build process-designer
curl localhost:3002/
```
