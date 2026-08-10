# reviewer-ui

Eigenständige Frontend-Anwendung mit schlankem Fokus nur auf Freigabeaufgaben
(Konzept 8: "dedizierte Reviewer/Approval-UI ... auch für Vier-Augen-Fälle"),
P14-S2. Zwei Bereiche:

- **Aufgaben** — bereite Manual-/Signature-Tasks über alle laufenden
  `workflow-service`-Prozessinstanzen hinweg (`GET /tasks`, neu seit dieser
  Session), inkl. Abschluss-Formular (Signatur-ID-Pflichtfeld bei Signature
  Tasks, 3.10).
- **Freigaben** — generische Vier-Augen-Inbox (4.3) über `permission-service`s
  `GET /approval-requests`, ungefiltert nach Aktionstyp (erster generischer
  Konsument dieser API im gesamten System).

Reines Client-Side-Rendering, kein Node-Prozess in Produktion (identisches
Muster wie `apps/user-ui`/`apps/process-designer`, siehe
[ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)).

Ausführliche Doku: [`docs/services/reviewer-ui.md`](../../docs/services/reviewer-ui.md).

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
cd ../../infra && docker compose up -d --build reviewer-ui
curl localhost:3005/
```
