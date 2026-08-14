# cmis-connector

Second reference connector of the connector architecture (concept 3.3, P12-S4): makes
`folder-service`/`document-service` accessible via a self-implemented CMIS 1.1
Browser Binding — the DMS is the CMIS **server**. No maintained
Python CMIS *server* library exists (see ADR 0036), so it was implemented
by hand, covering only a subset (~14 endpoints). Details: see
[`docs/services/cmis-connector.md`](../../docs/services/cmis-connector.md).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/browser` | All repositories (here always exactly one: `default`) |
| `GET` | `/browser/{repositoryId}` | Repository info |
| `GET` | `/browser/{repositoryId}/root[/{path}]` | Read object (`cmisselector=children\|object\|content`, or `?objectId=`) |
| `POST` | `/browser/{repositoryId}/root[/{path}]` | Write (`cmisaction=createDocument\|createFolder\|update\|move\|delete\|deleteTree\|setContent\|checkOut\|cancelCheckOut\|checkIn`) |
| `GET` | `/healthz` | Own health check (ungated) |

All `/browser/*` calls require HTTP basic auth (real `auth-service` credentials).

## Running locally

```bash
cd infra && docker compose up -d postgres nats document-service folder-service auth-service registry-service cmis-connector
curl localhost:8030/healthz
curl -u <user>:<password> "http://localhost:8030/browser/default/root?cmisselector=children"
```

## Tests

```bash
cd infra && docker compose up -d postgres nats document-service folder-service auth-service registry-service cmis-connector
cd ..
uv run pytest services/cmis-connector/tests
```

Runs against the real, running instance (raw HTTP calls in the browser binding wire format) — no
mocking of the protocol, no CMIS client library as a test dependency (see ADR 0036 for why
no maintained one exists).
