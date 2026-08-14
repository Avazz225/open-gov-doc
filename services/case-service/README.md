# case-service

Umlaufmappen (circulation folders, concept 2.3): bundle references (not their own copies) to
documents belonging to a case — the lifecycle runs through a
process instance in [workflow-service](../workflow-service/) (7.1, P6-S1).
While the Umlaufmappe is open, the current version of each referenced document is
resolved dynamically; upon reaching the BPMN end state,
the reference structure is fixed as a **completion snapshot**.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/cases` | Create (`name`, optional `object_type_id`/`attributes`, `process_definition_id`, `created_by`, optional `initial_data`) - starts a process instance in workflow-service |
| `GET` | `/cases` | List, filter by `status`/`object_type_id` |
| `GET` | `/cases/{id}` | Detail |
| `POST` | `/cases/{id}/documents` | Add document reference (`document_id`, `added_by`) |
| `DELETE` | `/cases/{id}/documents/{document_id}` | Remove reference (`removed_by`, soft delete) |
| `GET` | `/cases/{id}/documents` | References including resolved version (dynamic while open / fixed once closed) |
| `GET` | `/healthz` | Health check |

Details/schema/events/open items: see `../../docs/services/case-service.md`.

## Completion snapshot

case-service is the first consumer of workflow-service's
`workflow.instance.completed` event (see `consumer.py`). Since the
process instance is started with `business_key = case_id`, the
handler can directly locate the associated Umlaufmappe via this value
and fix the then-current version for each active document reference.

## Running locally

```bash
cd infra && docker compose up -d postgres nats registry-service workflow-service document-service object-type-service case-service
curl localhost:8016/healthz
```

## Tests

```bash
uv run pytest services/case-service/tests
```

Runs against a real Postgres instance rather than
mocks, like every other service. API tests that use `object_type_id` or a
real workflow start additionally require locally reachable `object-type-service`/
`workflow-service`/`document-service` instances (same pattern as
document-service's `folder_client`/`object_type_client` tests).
