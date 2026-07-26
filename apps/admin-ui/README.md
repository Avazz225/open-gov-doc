# admin-ui

Administrative Web-Oberfläche (Konzept 8): Nutzer-/Rollenverwaltung,
Objekttyp-Editor, Registry-Übersicht. Reines Client-Side-Rendering — Next.js
dient nur als React-Build-/Routing-Tooling (`output: "export"`), es läuft
**kein Node-Prozess in Produktion** (identisches Muster wie `apps/user-ui`,
siehe [ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)).

Ausführliche Doku: [`docs/services/admin-ui.md`](../../docs/services/admin-ui.md).

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
cd ../../infra && docker compose up -d --build admin-ui
curl localhost:3001/
```
