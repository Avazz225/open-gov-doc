# registry-service

Service Discovery (Konzept 3.2a): Registrierung, Heartbeat und die aktive
Routingtabelle je Servicetyp. Lizenzvermittlung (3.2b) folgt erst mit dem
License Service (Phase 9) und ist hier bewusst noch nicht enthalten.

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/instances` | Registrieren (Upsert nach `instance_id`) |
| `POST` | `/instances/{instance_id}/heartbeat` | Heartbeat senden |
| `DELETE` | `/instances/{instance_id}` | Deregistrieren |
| `GET` | `/instances/{service_type}` | Aktive Routingtabelle für einen Servicetyp |
| `GET` | `/instances` | Alle Instanzen inkl. `healthy`-Flag (Debug/Admin) |
| `GET` | `/healthz` | Eigener Health-Check |

**Ausfallerkennung ohne Hintergrundjob**: Eine Instanz gilt als ausgefallen,
wenn ihr letzter Heartbeat länger als `heartbeat_timeout_seconds`
(Default 15s) zurückliegt. Das wird beim Lesen berechnet, nicht durch einen
mutierenden Sweep-Prozess - vermeidet Race Conditions und ist einfacher zu
testen, bei identischem Ergebnis ("ausgefallene Instanzen erscheinen nicht in
der aktiven Routingtabelle").

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres registry-service
curl localhost:8001/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres && cd ..
uv run pytest services/registry-service/tests
```
