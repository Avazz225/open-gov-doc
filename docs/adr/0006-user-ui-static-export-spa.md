# 0006 — User UI: Next.js as a statically exported SPA under `apps/`, no Node runtime server

**Status:** accepted
**Context:** Concept 8, Session P4-S2 (User UI base scaffold)

## Decision

1. The project's first frontend (`apps/user-ui`) lives under a new top-level
   folder `apps/`, not under `services/` — `docs/service-template.md`
   (layout, `pyproject.toml`, Dockerfile) is explicitly tailored to Python
   services and does not fit a Node/React build toolchain.
2. Next.js is used exclusively as a React build/routing tool, with
   `output: "export"` (static export). There is **no Node process at
   runtime** — the production image is a two-stage build (Node only in the
   build stage, `nginx:alpine` serves the finished static files).
3. The application is a pure SPA: login state, folder navigation,
   upload/download all run entirely client-side against the API gateway
   (`/api/{service_type}/{path}`, see ADR 0005). There is no dedicated
   backend process for the UI itself.
4. Tokens (access + refresh) are stored in the browser's `localStorage`, not
   in an httpOnly cookie.

## Rationale

- Points 2 and 3 directly implement the decision **already made** in the
  concept (not merely recommended) in favor of client-side rendering: "SEO
  does not matter" (the application sits behind login) and "avoids an
  additional Node runtime layer in the backend" (the Python services should
  not have to be supplemented by an additional Node SSR process that
  re-fetches from them on every page load). A static export fulfills exactly
  this requirement without giving up React/Next.js as tooling.
- `apps/` instead of `services/`: avoids artificially bending the
  well-established Python service template (no `pyproject.toml`, no
  `pytest`, no `uv sync --package`). The definition of done from
  `CONTRIBUTING.md` (README, tests, Dockerfile, Compose entry, service docs)
  still applies in substance, just with Node-typical tooling (`npm`,
  `vitest`, `eslint`) instead of the Python equivalents.
- **localStorage instead of an httpOnly cookie for tokens**: An httpOnly
  cookie would need a server to set it (e.g. the gateway would need to offer
  a session endpoint that converts a login response into a cookie) — this
  would contradict the deliberately serverless delivery of this SPA (point
  2). `localStorage` is the pragmatic default solution for pure SPAs against
  a JSON API and is sufficient for a "base scaffold", but carries a known
  XSS risk (an injected script snippet could read out tokens). A deliberately
  documented simplification, not an overlooked gap.
- **Gateway address baked in at build time**
  (`NEXT_PUBLIC_GATEWAY_BASE_URL`): a consequence of "no server at runtime" —
  there is no process that could reload configuration at runtime. A
  different gateway endpoint (e.g. a different environment) requires an
  image rebuild with a different build arg, analogous to this project's other
  environment variable conventions, just evaluated at build time instead of
  runtime.

## Consequences

- Every future frontend application (Admin UI P4-S3, Reviewer UI, migration
  console, Concept 8) follows the same pattern (`apps/<name>`, static
  export, nginx delivery) — an analogous short guide could later be captured
  as `docs/frontend-template.md`, once a second frontend app exists (not yet
  part of this session, since a stable template cannot yet be derived from a
  single instance).
- A stronger auth model (httpOnly session cookie via the gateway, CSRF
  protection) is a later step, once security requirements go beyond the base
  scaffold — it would then require its own, no-longer-purely-static building
  block (e.g. a lightweight session endpoint in the gateway).
- No server-side data preloading is possible (no SSR) — every page briefly
  shows a loading state on first load, until the client-side fetch against
  the gateway completes. Unproblematic for the authenticated core
  application as envisioned in the concept.
- Rendering/preview (3.7/2.4) is deliberately present only as a stub (modal
  with placeholder text) — real previews (thumbnails, PDF rendering) follow
  with the Rendering/Preview Service (P5-S2) and a corresponding extension of
  this component, no redevelopment of the UI structure needed.
