# process-designer

Standalone frontend application for graphical BPMN 2.0 modeling
(Concept 7.1/8) — **not** part of the admin UI. Models process definitions
via [`bpmn-js`](https://bpmn.io) against the workflow engine from
`workflow-service` (P6-S1), including its own properties panel provider for
the Signature Task (3.10, P6-S7). Pure client-side rendering, no
Node process in production (identical pattern to `apps/user-ui`, see
[ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)).

Detailed documentation: [`docs/services/process-designer.md`](../../docs/services/process-designer.md).
Library decision (not `bpmn-js-spiffworkflow`): [ADR 0026](../../docs/adr/0026-process-designer-bpmn-js-without-spiffworkflow-addon.md).

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
cd ../../infra && docker compose up -d --build process-designer
curl localhost:3002/
```
