# webdav-connector

First reference connector of the connector architecture (Concept 3.3, P12-S1): makes
`folder-service`/`document-service` accessible as a network drive
(Windows Explorer/macOS Finder/Word) via the WebDAV protocol (RFC 4918) —
the DMS is the WebDAV **server**, not a client of an external repository.
For details, architecture decisions, and real implementation pitfalls
encountered, see
[`docs/services/webdav-connector.md`](../../docs/services/webdav-connector.md).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `*` | `/webdav/*` | WebDAV protocol (PROPFIND/GET/PUT/MKCOL/MOVE/DELETE/LOCK/UNLOCK), bridged via `asgiref.wsgi.WsgiToAsgi` in `wsgidav` |
| `GET` | `/healthz` | Own health check (ungated, no authentication needed) |

`/webdav/*` requires WebDAV basic auth (real `auth-service` credentials, see `DmsAuthDomainController`).

## Running Locally

```bash
cd infra && docker compose up -d postgres nats document-service folder-service auth-service registry-service webdav-connector
curl localhost:8027/healthz
# Mount, e.g. on Linux:
# mount -t davfs http://localhost:8027/webdav /mnt/dms -o username=<user>
```

## Tests

```bash
cd infra && docker compose up -d postgres nats document-service folder-service auth-service registry-service webdav-connector
cd ..
uv run pytest services/webdav-connector/tests
```

Real roundtrip via a real WebDAV client (`webdav4`, MIT, test dependency
only) against the running instance — no mocking of the protocol.
