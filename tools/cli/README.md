# tools/cli/

Das DMS-CLI-Tool (Konzept 6.2) — ein Client gegen das API-Gateway (3.5), konzeptionell
an `oc` (OpenShift) orientiert: dieselben Rechte/Sicherungsstufen wie die Web-UIs, da
jeder Aufruf über dieselbe Gateway-Route (`/api/{service_type}/{path}`) mit Bearer-Token
läuft. Details, Kommandoübersicht und bewusst nicht abgedeckte Konzeptpunkte siehe
[`docs/tools/cli.md`](../../docs/tools/cli.md).

## Installation/Ausführung

```bash
uv sync --all-packages
uv run --package dms-cli dms --help
```

Als installierter Konsolenbefehl (nach `uv pip install -e tools/cli` oder `uv tool install`)
einfach `dms ...`. Alternativ als Docker-Image (siehe `Dockerfile`, kein Compose-Dienst):

```bash
docker build -f tools/cli/Dockerfile -t dms-cli .
docker run --rm -e DMS_GATEWAY_URL=http://host.docker.internal:8009 -e DMS_TOKEN=... \
  dms-cli query events list
```

## Anmeldung

```bash
dms login --username alice --gateway-url http://localhost:8009
dms whoami
```

Zugangsdaten liegen danach in `~/.dms/credentials.json` (chmod 600). `DMS_GATEWAY_URL`/
`DMS_TOKEN` überschreiben das für CI/CD-Pipelines, die einen anderweitig beschafften Token
injizieren (siehe "Offene Punkte" in `docs/tools/cli.md` zum Fehlen eines echten
Service-Account-Grants).

## Tests

```bash
uv run pytest tools/cli/tests
uv run ruff check tools/cli
uv run ruff format --check tools/cli
```
