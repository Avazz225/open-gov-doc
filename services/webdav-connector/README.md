# webdav-connector

Erster Referenz-Connector der Connector-Architektur (Konzept 3.3, P12-S1): macht
`folder-service`/`document-service` über das WebDAV-Protokoll (RFC 4918) als
Netzlaufwerk ansprechbar (Windows-Explorer/macOS-Finder/Word) — das DMS ist der
WebDAV-**Server**, kein Client eines externen Repositories. Details, Architektur-
entscheidungen und real aufgetretene Implementierungsfallen siehe
[`docs/services/webdav-connector.md`](../../docs/services/webdav-connector.md).

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `*` | `/webdav/*` | WebDAV-Protokoll (PROPFIND/GET/PUT/MKCOL/MOVE/DELETE/LOCK/UNLOCK), gebrückt über `asgiref.wsgi.WsgiToAsgi` in `wsgidav` |
| `GET` | `/healthz` | Eigener Health-Check (ungegatet, keine Authentifizierung nötig) |

`/webdav/*` verlangt WebDAV-Basic-Auth (echte `auth-service`-Zugangsdaten, siehe `DmsAuthDomainController`).

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats document-service folder-service auth-service registry-service webdav-connector
curl localhost:8027/healthz
# Mounten, z. B. unter Linux:
# mount -t davfs http://localhost:8027/webdav /mnt/dms -o username=<user>
```

## Tests

```bash
cd infra && docker compose up -d postgres nats document-service folder-service auth-service registry-service webdav-connector
cd ..
uv run pytest services/webdav-connector/tests
```

Echter Roundtrip über einen echten WebDAV-Client (`webdav4`, MIT, nur Test-
Abhängigkeit) gegen die laufende Instanz — kein Mocking des Protokolls.
