# license-service

License management/verification (Concept 9.1/9.2): signed license file
(JWT/RS256, [ADR 0032](../../docs/adr/0032-lizenzdatei-signaturverfahren.md)),
ongoing usage checks against four dimensions (user count,
application components, storage volume, document count). Details in
[`docs/services/license-service.md`](../../docs/services/license-service.md).

## Endpoints

- `POST /license` — install a signed license file (`admin.license` or activated superuser).
- `GET /license/status` — current license status + usage per dimension (ungated).

## Tests

```bash
uv run pytest services/license-service/tests
```
