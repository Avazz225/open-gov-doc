# user-ui

Authentifizierte Web-Oberfläche für Endnutzer (Konzept 8): Anmelden, Ordner-
Navigation, Dokument-Upload/-Download, Vorschau-Platzhalter. Reines
Client-Side-Rendering — Next.js dient nur als React-Build-/Routing-Tooling
(`output: "export"`), es läuft **kein Node-Prozess in Produktion** (siehe
[ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)).

Ausführliche Doku: [`docs/services/user-ui.md`](../../docs/services/user-ui.md).

## Lokale Entwicklung

```bash
npm install
npm run dev
```

Erwartet ein laufendes Gateway auf `http://localhost:8009` (Default aus
`src/lib/config.ts`, überschreibbar über `NEXT_PUBLIC_GATEWAY_BASE_URL`):

```bash
cd ../../infra && docker compose up -d
```

## Build (statischer Export)

```bash
npm run build
```

Erzeugt `out/` — ausgeliefert im Produktions-Image über `nginx` (siehe
`Dockerfile`), keine Laufzeit-Node-Abhängigkeit.

## Tests

```bash
npm run typecheck
npm run lint
npm test
```

Vitest + Testing Library, Netzwerkschicht (`fetch`) gemockt (Grenze zur
externen Infrastruktur, analog zu `dms-auth-client`s lokalen Test-Schlüsseln
statt echtem Keycloak). Für eine echte End-to-End-Verifikation gegen den
laufenden Compose-Stack ist ein Browser nötig (Playwright) — in der
aktuellen Entwicklungsumgebung nicht verfügbar, siehe
`docs/services/user-ui.md` für die stattdessen durchgeführte curl-basierte
Verifikation jedes einzelnen Gateway-Aufrufs.

## Docker

```bash
cd ../../infra && docker compose up -d --build user-ui
curl localhost:3000/
```
