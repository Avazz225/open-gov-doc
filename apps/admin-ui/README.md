# admin-ui

Administrative web interface (Concept 8): user/role management,
object type editor, registry overview. Pure client-side rendering — Next.js
serves only as React build/routing tooling (`output: "export"`),
**no Node process runs in production** (identical pattern to `apps/user-ui`,
see [ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)).

Detailed documentation: [`docs/services/admin-ui.md`](../../docs/services/admin-ui.md).

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
cd ../../infra && docker compose up -d --build admin-ui
curl localhost:3001/
```
