# permission-service

RBAC mit Ordner-Vererbung und materialisiertem, ereignisgetriebenem Rechte-Cache
(Konzept 4.1).

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/roles` | Rolle anlegen (`name`, `description`, `permissions: [str]`) |
| `GET` | `/roles` | Alle Rollen |
| `POST` | `/role-assignments` | Rolle einem Principal an einer Ressource zuweisen |
| `DELETE` | `/role-assignments/{id}` | Zuweisung entfernen |
| `GET` | `/resources/{id}` | Ressourcenknoten (Debug/Test) |
| `PATCH` | `/resources/{id}` | Vererbung ein-/ausschalten (`inherit: bool`) |
| `GET` | `/effective-permissions/{principal_id}/{resource_id}` | Effektive Rollen/Rechte (gecacht) |
| `GET` | `/check?principal_id=&resource_id=&permission=` | Autorisierungs-Check |
| `GET` | `/healthz` | Eigener Health-Check |

## Vererbungsmodell

Standard-DMS-Verhalten (SharePoint/Alfresco-artig): Rechte vererben sich von
der Wurzel (`root`, beim Start automatisch angelegt) nach unten. Ein
Ressourcenknoten mit `inherit=false` bricht die Vererbung an dieser Stelle ab
- eigene Zuweisungen an genau diesem Knoten gelten weiterhin, nur der weitere
Aufstieg zu Vorfahren entfällt.

## Ressourcen-Hierarchie: Folder Service (seit P3-S3)

Dieser Service hält seine `resource_node`-Tabelle über Struktur-Events synchron,
die der Folder Service publiziert (`folder.resource.created/.moved/.deleted`,
siehe `docs/services/permission-service.md` — Vertrag in P3-S3 live gegen die
echte Folder-Service-API verifiziert, keine Anpassung nötig). Startet dieser
Service, bevor je ein Producer den `folder`-Stream angelegt hat (z. B. beim
allerersten Hochfahren des gesamten Stacks), wird das Abonnement übersprungen
(siehe `structure_consumer.py`) statt den Start zu blockieren — ein Neustart
nach dem ersten Start des Folder Service holt das nach.

## Cache-Invalidierung

Grobkörnig: jede Rechte- oder Strukturänderung leert den gesamten
`effective_permission_cache` statt nur den betroffenen Teilbaum. Bewusste
Vereinfachung für den Start - korrekt, aber nicht maximal granular.

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats permission-service
curl localhost:8004/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats && cd ..
uv run pytest services/permission-service/tests
```
