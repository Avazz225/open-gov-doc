# cmis-connector

Zweiter Referenz-Connector der Connector-Architektur (Konzept 3.3, P12-S4): macht
`folder-service`/`document-service` über eine selbst implementierte CMIS 1.1
Browser Binding ansprechbar — das DMS ist der CMIS-**Server**. Keine gepflegte
Python-CMIS-*Server*-Bibliothek existiert (siehe ADR 0036), daher von Hand
implementiert, nur ein Teilumfang (~14 Endpunkte). Details siehe
[`docs/services/cmis-connector.md`](../../docs/services/cmis-connector.md).

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/browser` | Alle Repositories (hier immer genau eines: `default`) |
| `GET` | `/browser/{repositoryId}` | Repository-Info |
| `GET` | `/browser/{repositoryId}/root[/{path}]` | Objekt lesen (`cmisselector=children\|object\|content`, oder `?objectId=`) |
| `POST` | `/browser/{repositoryId}/root[/{path}]` | Schreiben (`cmisaction=createDocument\|createFolder\|update\|move\|delete\|deleteTree\|setContent\|checkOut\|cancelCheckOut\|checkIn`) |
| `GET` | `/healthz` | Eigener Health-Check (ungegatet) |

Alle `/browser/*`-Aufrufe verlangen HTTP-Basic-Auth (echte `auth-service`-Zugangsdaten).

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats document-service folder-service auth-service registry-service cmis-connector
curl localhost:8030/healthz
curl -u <user>:<passwort> "http://localhost:8030/browser/default/root?cmisselector=children"
```

## Tests

```bash
cd infra && docker compose up -d postgres nats document-service folder-service auth-service registry-service cmis-connector
cd ..
uv run pytest services/cmis-connector/tests
```

Läuft gegen die echte, laufende Instanz (rohe HTTP-Aufrufe im Browser-Binding-Wire-Format) — kein
Mocking des Protokolls, keine CMIS-Client-Bibliothek als Testabhängigkeit (siehe ADR 0036, warum
keine gepflegte existiert).
