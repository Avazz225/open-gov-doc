# license-service

Lizenzverwaltung/-pruefung (Konzept 9.1/9.2): signierte Lizenzdatei
(JWT/RS256, [ADR 0032](../../docs/adr/0032-lizenzdatei-signaturverfahren.md)),
laufende Nutzungspruefung gegen vier Dimensionen (Nutzeranzahl,
Applikationskomponenten, Speichervolumen, Dokumentenzahl). Details siehe
[`docs/services/license-service.md`](../../docs/services/license-service.md).

## Endpunkte

- `POST /license` — signierte Lizenzdatei installieren (`admin.license` oder aktivierter Superuser).
- `GET /license/status` — aktueller Lizenzstatus + Nutzung je Dimension (ungegatet).

## Tests

```bash
uv run pytest services/license-service/tests
```
