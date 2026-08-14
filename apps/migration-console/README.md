# migration-console

Standalone frontend application for transfer operations (Concept 7.2/8:
"Migration console for transfer operations"), P14-S2. Two sections:

- **Transfers** — overview/start of new migration/handover operations against
  `migration-service` (`POST/GET /transfers`), including dry run, optional
  deletion deadline, four-eyes notice (4.3) and detail view (progress,
  phase timeline, error message on `failed`). Lightweight polling
  every 5s, since a transfer itself continues running in the background as an
  asynchronous `workflow-service` process.
- **Paired installations** — installation pairing (7.2, direct pairing
  instead of hub mediation, [ADR 0034](../../docs/adr/0034-migration-service-direct-pairing-and-generic-connector-service-tasks.md)):
  create/remove, one-time display of a generated API key.

Pure client-side rendering, no Node process in production (identical
pattern to `apps/user-ui`/`apps/process-designer`/`apps/reviewer-ui`, see
[ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)).

Detailed documentation: [`docs/services/migration-console.md`](../../docs/services/migration-console.md).

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
cd ../../infra && docker compose up -d --build migration-console
curl localhost:3004/
```
