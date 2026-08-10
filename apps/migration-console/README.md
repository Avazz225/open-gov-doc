# migration-console

Eigenständige Frontend-Anwendung für Transfer-Vorgänge (Konzept 7.2/8:
"Migrations-Konsole für Transfer-Vorgänge"), P14-S2. Zwei Bereiche:

- **Transfers** — Übersicht/Start neuer Migrations-/Übergabevorgänge gegen
  `migration-service` (`POST/GET /transfers`), inkl. Dry-Run, optionaler
  Löschfrist, Vier-Augen-Hinweis (4.3) und Detailansicht (Fortschritt,
  Phasen-Zeitverlauf, Fehlermeldung bei `failed`). Leichtgewichtiges Polling
  alle 5s, da ein Transfer selbst als asynchroner `workflow-service`-Prozess
  im Hintergrund weiterläuft.
- **Gepaarte Installationen** — Installations-Paarung (7.2, direktes Paar
  statt Hub-Vermittlung, [ADR 0034](../../docs/adr/0034-migration-service-direct-pairing-and-generic-connector-service-tasks.md)):
  anlegen/entfernen, einmalige Anzeige eines generierten API-Keys.

Reines Client-Side-Rendering, kein Node-Prozess in Produktion (identisches
Muster wie `apps/user-ui`/`apps/process-designer`/`apps/reviewer-ui`, siehe
[ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)).

Ausführliche Doku: [`docs/services/migration-console.md`](../../docs/services/migration-console.md).

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
cd ../../infra && docker compose up -d --build migration-console
curl localhost:3004/
```
