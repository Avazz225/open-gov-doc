# tools/cli/

The DMS CLI tool (Concept 6.2) — a client against the API Gateway (3.5), conceptually
modeled after `oc` (OpenShift): the same permissions/security levels as the web UIs, since
every call goes through the same gateway route (`/api/{service_type}/{path}`) with a bearer token.
For details, a command overview, and concept points deliberately not covered, see
[`docs/tools/cli.md`](../../docs/tools/cli.md).

## Installation/Running

```bash
uv sync --all-packages
uv run --package dms-cli dms --help
```

As an installed console command (after `uv pip install -e tools/cli` or `uv tool install`)
simply `dms ...`. Alternatively as a Docker image (see `Dockerfile`, no compose service):

```bash
docker build -f tools/cli/Dockerfile -t dms-cli .
docker run --rm -e DMS_GATEWAY_URL=http://host.docker.internal:8009 -e DMS_TOKEN=... \
  dms-cli query events list
```

## Login

```bash
dms login --username alice --gateway-url http://localhost:8009
dms whoami
```

Credentials are then stored in `~/.dms/credentials.json` (chmod 600). `DMS_GATEWAY_URL`/
`DMS_TOKEN` override this for CI/CD pipelines that inject a token obtained by other means
(see "Open Items" in `docs/tools/cli.md` regarding the absence of a real
service account grant).

## Tests

```bash
uv run pytest tools/cli/tests
uv run ruff check tools/cli
uv run ruff format --check tools/cli
```
