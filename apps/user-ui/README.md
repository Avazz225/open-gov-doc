# user-ui

Authenticated web interface for end users (Concept 8): sign in, folder
navigation, document upload/download, preview placeholder. Pure
client-side rendering — Next.js serves only as React build/routing tooling
(`output: "export"`), **no Node process runs in production** (see
[ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)).

Detailed documentation: [`docs/services/user-ui.md`](../../docs/services/user-ui.md).

## Local Development

```bash
npm install
npm run dev
```

Expects a running gateway at `http://localhost:8009` (default from
`src/lib/config.ts`, overridable via `NEXT_PUBLIC_GATEWAY_BASE_URL`):

```bash
cd ../../infra && docker compose up -d
```

## Build (static export)

```bash
npm run build
```

Produces `out/` — served in the production image via `nginx` (see
`Dockerfile`), no runtime Node dependency.

## Tests

```bash
npm run typecheck
npm run lint
npm test
```

Vitest + Testing Library, network layer (`fetch`) mocked (boundary to
external infrastructure, analogous to `dms-auth-client`'s local test keys
instead of a real Keycloak). A real end-to-end verification against the
running Compose stack requires a browser (Playwright) — not available in the
current development environment, see
`docs/services/user-ui.md` for the curl-based verification of each
individual gateway call performed instead.

## Docker

```bash
cd ../../infra && docker compose up -d --build user-ui
curl localhost:3000/
```
