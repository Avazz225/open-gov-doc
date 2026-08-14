# office-addin

Microsoft Office add-in (Office.js, **Word** only) for native OG Doc integration
(Concept 3.3a), P14-S8. Open/save a document directly from/to
OG Doc, inline metadata editing, workflow start/continuation, central
role-based template library — without having to open the DMS interface
separately. Talks exclusively to already existing `document-service`/
`workflow-service`/`object-type-service`/`folder-service`/`search-service`
endpoints, no new backend code (see
[ADR 0045](../../docs/adr/0045-office-addin-word-only-reused-endpoints-settings-linking.md)).

Pure client-side rendering, no Node process in production (identical
pattern to `apps/user-ui`, ADR 0006).

Detailed documentation: [`docs/services/office-addin.md`](../../docs/services/office-addin.md).

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
npx office-addin-manifest validate manifest.xml
```

## Docker

```bash
cd ../../infra && docker compose up -d --build office-addin
curl localhost:3006/
```

## Local Sideload Testing (Real Word Host)

Office only loads add-in web content over HTTPS. This stack runs
entirely over HTTP in development like every other service — before a real
sideload test in Word, two additional steps are needed that are **not**
part of `docker compose up`:

1. **Provide TLS**, e.g. with Microsoft's own development certificate
   tool (creates a locally trusted certificate):
   ```bash
   npx office-addin-dev-certs install
   ```
   and a reverse proxy (e.g. `nginx`/`caddy`) in front of it that terminates
   HTTPS on port 3006 - or replace `npm run dev` with an HTTPS-capable
   dev server.
2. **Adjust `manifest.xml`**: replace all `https://localhost:3006`
   placeholders (`IconUrl`, `SourceLocation`, `bt:Url`, `AppDomain`) with the
   actually reachable HTTPS address.

Then sideload:

```bash
npx office-addin-debugging start manifest.xml desktop
```

opens Word and activates the add-in automatically. Alternatively, do it manually via
Word → Insert → My Add-ins → Upload My Add-in → select `manifest.xml`.

**Important**: In this development environment (no Windows/Office installed),
this step itself could not be carried out - only the
manifest structure was validated with the official `office-addin-manifest` tool
(`npx office-addin-manifest validate manifest.xml` → "The manifest
is valid."). A human should actually perform the sideload test once
before production use.
