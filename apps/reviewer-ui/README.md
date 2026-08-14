# reviewer-ui

Standalone frontend application with a lean focus purely on approval tasks
(Concept 8: "dedicated reviewer/approval UI ... also for four-eyes cases"),
P14-S2. Two sections:

- **Tasks** — ready manual/signature tasks across all running
  `workflow-service` process instances (`GET /tasks`, new as of this
  session), including completion form (signature ID required field for
  signature tasks, 3.10).
- **Approvals** — generic four-eyes inbox (4.3) via `permission-service`'s
  `GET /approval-requests`, unfiltered by action type (first generic
  consumer of this API in the entire system).

Pure client-side rendering, no Node process in production (identical
pattern to `apps/user-ui`/`apps/process-designer`, see
[ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)).

Detailed documentation: [`docs/services/reviewer-ui.md`](../../docs/services/reviewer-ui.md).

## Local Development

```bash
npm install
npm run dev
```

Expects a running gateway at `http://localhost:8009`:

```bash
cd ../../infra && docker compose up -d
```

## Build (static export)

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
