# dms-permission-client

HTTP-Client gegen `permission-service` (RBAC-Prüfung, Rollenzuweisung) - konsolidiert die zuvor
siebenfach je Service duplizierte `PermissionServiceClient`-Klasse (Post-Roadmap Phase 19 Session 1).

- `PermissionServiceClient(base_url, *, timeout=30.0, client=None)` - `client` erlaubt das Injizieren
  eines vorbereiteten `httpx.AsyncClient` (z. B. mit `httpx.MockTransport` für Tests, siehe
  `libs/dms-metrics-client`s `SensorConfigClient` für dasselbe Muster).
  - `check(*, principal_id, resource_id, permission, access_type="read") -> bool` - Einzelprüfung
    gegen `GET /check`.
  - `check_batch(*, principal_id, permission, access_type="read", resource_ids) -> dict[str, bool]` -
    Sammelprüfung gegen `POST /check/batch`, leeres Ergebnis ohne Serveraufruf bei leerer Liste.
  - `has_permission(principal_id, permission) -> bool` - Domain-Admin-Gate gegen
    `GET /effective-permissions/{principal_id}/root`.
  - `ensure_role_assignment(*, principal_id, role_name, resource_id="root") -> None` - idempotent,
    wirft `RoleNotFoundError` bei unbekannter Rolle bzw. `RoleAssignmentPendingApprovalError`, wenn
    die Installation `permission.role_assignment.create` Vier-Augen-pflichtig konfiguriert hat (ADR
    0060) und die Zuweisung dadurch noch nicht wirksam ist.
  - `get_role_id(name) -> int | None`, `close()`.

**Bewusst kein Zwangsrefactor der bestehenden Duplikate**: `document-service`, `search-service`,
`workflow-service`, `config-service`, `license-service`, `monitoring-service`,
`plugin-orchestration-service`, `query-service`, `teamspace-service` und `auth-service` behalten
vorerst ihre eigene, teils service-spezifisch erweiterte `PermissionServiceClient`-Klasse (z. B.
`workflow-service`s `check_delegation`, `query-service`s Vier-Augen-Endpunkte,
`teamspace-service`s Rollen-Bootstrap) - eine reine Umstellung ohne fachlichen Mehrwert wurde bewusst
nicht vorgenommen. Neue Konsumenten (Phase 19 ab Session 2) nutzen direkt dieses Paket.

## Nutzung

```python
from dms_permission_client import PermissionServiceClient

client = PermissionServiceClient(settings.permission_service_base_url)
allowed = await client.check(principal_id=user_id, resource_id=folder_id, permission="folder.read")
```

## Tests

Rein auf Unit-Ebene mit `httpx.MockTransport` (kein echter `permission-service` nötig):

```bash
uv run pytest libs/dms-permission-client/tests
```
