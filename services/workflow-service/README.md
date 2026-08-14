# workflow-service

Workflow engine foundation (Concept 7.1): BPMN 2.0 import and execution
via [SpiffWorkflow](https://github.com/sartography/SpiffWorkflow) (LGPLv3,
see [ADR 0018](../../docs/adr/0018-spiffworkflow-lgpl-license.md)), Manual/
Automatic Tasks. Pure backend foundation - no UI, no role checking
(only from [P6-S4–S6](../../PROGRESS.md)), no Process Designer (only from P6-S8).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/process-definitions` | Create (multipart: `bpmn_xml` file, `name`, optional `process_id`) |
| `GET` | `/process-definitions` | List (metadata) |
| `GET` | `/process-definitions/{id}` | Detail incl. `bpmn_xml` |
| `DELETE` | `/process-definitions/{id}` | Delete - `409` if instances exist |
| `POST` | `/process-definitions/{id}/instances` | Start instance (`created_by`, optional `business_key`/`initial_data`) |
| `GET` | `/instances/{id}` | Status/metadata |
| `GET` | `/instances?process_definition_id=&status=&business_key=` | Filtered list |
| `GET` | `/instances/{id}/tasks` | Ready Manual/User Tasks |
| `POST` | `/instances/{id}/tasks/{task_id}/complete` | Complete task (`completed_by`, optional `data`) |
| `GET` | `/healthz` | Health check |

Details/schema/events/open items: see `../../docs/services/workflow-service.md`.

## SpiffWorkflow Adapter

The entire SpiffWorkflow API surface is isolated in `src/workflow_service/spiff_adapter.py`
(parsing, execution, serialization) - `repository.py` itself has no direct knowledge of
SpiffWorkflow. See the file's docstring for the method names verified against the
installed version (3.1.2).

## State Persistence (ADR 0019)

Every process instance stores the complete execution state serialized by
SpiffWorkflow as a JSON blob - no separate, normalized task table. Ready
tasks are derived live from this blob on every read.

## Running Locally

```bash
cd infra && docker compose up -d postgres nats registry-service workflow-service
curl localhost:8014/healthz
```

## Tests

```bash
uv run pytest services/workflow-service/tests
```

`test_spiff_adapter.py` tests the adapter in isolation against real BPMN test fixtures
(`tests/fixtures/`, taken from the official SpiffWorkflow repo), independent of the
database/API. `test_repository.py`/`test_api.py` run against a real Postgres instance
(no mocking) - as with every other service, never against the running development
database, see `PROGRESS.md` "Tooling & Testing".
