# document-service

Dokumente als Kernentität (Konzept 2.1): CRUD, dauerhafte Versionierung (2.1a),
Bearbeitungssperre inkl. Force-Unlock und Konfliktkopie (4.2). Hält selbst nie
Dateiinhalte - jeder Zugriff läuft über die HTTP-API des Storage Service (3.6).

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/documents` | Anlegen (multipart: `file`, `title`, `created_by`, ...) |
| `GET` | `/documents/{id}` | Metadaten |
| `DELETE` | `/documents/{id}?deleted_by=...` | Weiche Löschung |
| `GET` | `/documents/{id}/content` | Inhalt der aktuellen Version |
| `GET` | `/documents/{id}/versions` | Alle Versionen (auch Konfliktkopien) |
| `POST` | `/documents/{id}/versions` | Check-in (multipart: `file`, `expected_base_version_number`, `created_by`) |
| `GET`/`POST`/`DELETE` | `/documents/{id}/lock` | Sperre lesen/setzen/regulär freigeben |
| `POST` | `/documents/{id}/lock/force-release` | Administrativer Force-Unlock |
| `GET` | `/healthz` | Health-Check |

Details/Schema/Events: siehe `../../docs/services/document-service.md`.

## Konfliktschutz statt "überwachter" Sperre

Das Konzept beschreibt Force-Unlock über einen dritten Lock-Zustand
("aufgehoben, aber überwacht"). Dieser Service verzichtet bewusst darauf und
löst denselben Datenschutz stattdessen über eine immer aktive, optimistische
Versionsprüfung beim Check-in: Jeder Upload gibt an, auf welcher Version er
basiert (`expected_base_version_number`); weicht das von der inzwischen
aktuellen Hauptversion ab, entsteht eine eigenständige Konfliktkopie statt
eines stillen Überschreibens. Begründung: siehe
`../../docs/adr/0002-document-locking-optimistic-conflict-detection.md`.

## Speicherung: inhaltsadressierte Objektschlüssel

Objekte werden unter `documents/{document_id}/{sha256}` im Storage Service
abgelegt - vermeidet die Reihenfolge-Abhängigkeit "Schlüssel braucht
Versionsnummer, Versionsnummer braucht abgeschlossenen DB-Schreibzugriff" und
dedupliziert identische Inhalte automatisch.

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats minio storage-service document-service
curl localhost:8006/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats minio storage-service && cd ..
uv run pytest services/document-service/tests
```

Alle Tests laufen gegen echte Infrastruktur (Postgres, NATS, Storage Service
über HTTP) - keine Mocks.
