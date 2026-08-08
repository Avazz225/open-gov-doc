# Backup & Restore (Konzept 10.4, P11-S3)

Konzept 10.4 verlangt koordinierte Sicherung von Shared DB, Storage-Backends und
Konfiguration, eine feste Wiederherstellungsreihenfolge und einen Wartungsmodus während
des Vorgangs. Diese Seite beschreibt den real umgesetzten Mechanismus und zieht eine
bewusste Grenze zu P11-S4 ("Löschabgleich nach Restore + automatisierte Restore-Tests").

## Architekturentscheidung bei Sessionstart

Zwei Rückfragen wurden vor der Umsetzung geklärt:

1. **Operative Skripte statt dauerhaft laufender Service**: `scripts/backup.sh`/
   `scripts/restore.sh`, kein neuer `backup-service` mit stehenden privilegierten
   Postgres-/Storage-Credentials — gleiches Muster wie `scripts/rolling-update.sh`
   (P10-S3) und die bewusste Docker-Socket-Abstinenz aus P10-S1.
2. **Scope-Abgrenzung zu P11-S4**: die Roadmap trennt bereits sauber zwischen dieser
   Session (Sicherungs-/Wiederherstellungs-*Mechanismus*) und P11-S4 (**Löschabgleich
   nach Restore** + **automatisierte Restore-Tests**). Diese Session verifiziert den
   Mechanismus real, aber einmalig und gegen eine isolierte Scratch-Umgebung — nicht
   gegen den laufenden Dev-Stack (die einzige gemeinsame Postgres-Instanz aller 26
   Services; ein echtes Überschreiben wäre destruktiv für die laufende Session).

## Ein Korrektur gegenüber der ursprünglichen Planung: kein Wartungsmodus beim Backup

Konzept 10.4s eigener Text verknüpft den Wartungsmodus explizit nur mit dem **Restore**
("lösen bei Restore automatisch einen Wartungsmodus aus ... regulärer Nutzerzugriff
bleibt bis zum Abschluss des Löschabgleichs gesperrt"), nicht mit dem Backup selbst. Ein
WAL-basiertes Backup (`pg_basebackup` + kontinuierliche WAL-Archivierung) ist außerdem
by design ein **Online-/Hot-Backup** — die Konsistenz entsteht durch WAL-Replay bei der
Wiederherstellung, nicht durch Einfrieren der Schreibzugriffe während der Sicherung. Ein
ursprünglich geplanter kurzer Wartungsmodus-Trigger während `backup.sh` wurde deshalb
wieder verworfen (siehe PROGRESS.md) — er hätte außerdem eine reale Hürde bedeutet:
`POST /maintenance-mode/lift` (permission-service) verlangt zwingend den aktuell
**aktivierten Superuser** (4.8), dessen Aktivierung ihrerseits ein echtes
Break-Glass-Vier-Augen-Verfahren durchläuft (4.6) — unangemessen schwergewichtig für
eine routinemäßige Backup-Operation. Da `restore.sh` in dieser Session ausschließlich
gegen eine isolierte Scratch-Umgebung arbeitet (siehe unten), berührt es den laufenden
Stack ebenfalls nicht und braucht den Wartungsmodus folgerichtig auch nicht. Ein echter
Produktions-Restore (P11-S4-Kontext) würde ihn brauchen — dokumentiert als offener
Anschlusspunkt.

## Was gesichert wird

- **Shared Database**: `infra/docker-compose.yml`s `postgres`-Service hat jetzt
  kontinuierliche WAL-Archivierung (`archive_mode=on`, `wal_level=replica`,
  `archive_command='test ! -f /wal-archive/%f && cp %p /wal-archive/%f'`, Volume
  `postgres-wal-archive`). Ein neuer `postgres-wal-archive-init`-Dienst behebt
  reproduzierbar die Standard-Root-Eigentümerschaft eines frisch angelegten
  Docker-Volumes (echter, live entdeckter Fehlschlag beim ersten Start: `cp: can't
  create '/wal-archive/...': Permission denied`) — `postgres` wartet jetzt darauf
  (`depends_on: condition: service_completed_successfully`).
- **Storage-Backends**: `scripts/backup.sh` liest `storage-service`s `DMS_TARGETS`
  und sichert jedes Ziel mit `role != "archive"` (die Archiv-Rolle, 5.6, dient der
  Aussonderung, nicht dem Schutz vor logischen Fehlern — bewusst ein anderer Zweck).
  Nur `type="local"` ist in dieser Session implementiert und verifiziert (der
  aktuell in diesem Stack tatsächlich aktive Zieltyp); `type="s3"` wird erkannt,
  aber übersprungen und als Warnung gemeldet — dokumentierte Lücke, kein
  stillschweigend fehlendes Verhalten.
- **Konfiguration**: **kein neuer Mechanismus**. Praktisch die gesamte
  Laufzeitkonfiguration liegt bereits in Postgres-Singleton-Tabellen
  (`TrashConfig`/`RetentionConfig`/`UploadConfig`/`sensor_config` u. a.) und ist
  damit automatisch Teil der DB-Sicherung. Der vom Konzept gemeinte *unabhängige*
  Konfigurationsexport braucht 7.3 (Konfigurationsimport/-export-Service, existiert
  erst ab P12-S3, gleiches Rückwärtsabhängigkeits-Muster wie der P10-S0-Fund zu
  10.1) — `manifest.json` trägt dafür schon ein `config_export: null`-Platzhalterfeld.
- **Keycloak**: unverändert automatisch mit erfasst (nutzt dieselbe Postgres-Instanz).

## `scripts/backup.sh`

```bash
scripts/backup.sh [--dest DIR]   # Default: ./backups/<timestamp>/
```

Ablauf: Storage-Ziele sichern (`docker exec storage-service tar czf - -C <base_path> .`)
→ `pg_basebackup` gegen den laufenden Postgres-Container (online) → aktuelle WAL-LSN
notieren (`pg_current_wal_lsn()`, der "Konsistenzanker" aus 10.4) → `manifest.json`
schreiben. `manifest.json` ist die einzige Quelle der Wahrheit, welche Artefakte
zusammengehören — genau das, was 10.4 als Hauptfehlerquelle nennt ("ein Restore auf
zeitlich unterschiedliche Stände von Storage und DB").

## `scripts/restore.sh`

```bash
scripts/restore.sh <backup-dir> [--recovery-target-time "2026-08-08 17:30:00+00"]
```

Deckt aus der 10.4-Reihenfolge die Schritte 1-3 ab, gegen eine **isolierte
Scratch-Umgebung**, nicht den laufenden Stack:

1. Storage-Tarballs in ein temporäres Verzeichnis entpacken.
2. Einen zweiten, temporären Postgres-Container (`dms-postgres-restore-test`, eigenes
   Scratch-Volume, kein `dms-net`, kein Host-Port) aus dem Basebackup aufbauen, mit
   `recovery.signal` + `restore_command`/`recovery_target_time`/
   `recovery_target_action=promote` — echtes Point-in-Time-Recovery über das
   WAL-Archiv-Volume (read-only gemountet).
3. Storage-Checksummen aus der **wiederhergestellten** `storage.object_metadata`-Tabelle
   gegen die entpackten Dateien prüfen — beweist, dass DB- und Storage-Restore
   tatsächlich konsistent zueinander stehen.
4. Scratch-Container/-Volumes/-Verzeichnisse werden am Ende immer aufgeräumt (`trap`).

## Bewusst nicht Teil dieser Session (Zuständigkeit P11-S4)

- **Löschabgleich (Schritt 4, Konzept 10.4)**: der verpflichtende Abgleich gegen das
  Löschregister (`document_service.models.DeletionRegisterEntry`, P7-S1) nach jedem
  Restore, damit ein Restore nie eine bereits rechtmäßig zwangsgelöschte Zeile
  "wiederauferstehen" lässt. Braucht eine eigene Sonderbehandlung dieser Tabelle
  (P11-S0-Befund: "nicht Backup-differenziert" — noch offen).
- **Suchindex-Neuaufbau (Schritt 7)**: würde eine volle Wiederanbindung des App-Stacks
  an eine wiederhergestellte DB voraussetzen (der Suchindex selbst braucht kein
  eigenes Backup, da rekonstruierbar) — das ist Kern von P11-S4s "automatisierten
  Restore-Tests", nicht dieser Session.
- **Event-Bus-Reset (Schritt 5) / Registry-Neustart (Schritt 6)**: für eine isolierte
  Scratch-Verifikation nicht anwendbar (kein NATS/keine App-Services beteiligt) — nur
  in Konzept-Reihenfolge dokumentiert, nicht geskriptet.
- **Wartungsmodus während eines echten Produktions-Restores**: siehe Abschnitt oben —
  wird von einem echten Restore-Vorgang gebraucht, aber nicht von dieser Skript-
  gestützten Scratch-Verifikation.
- **Automatisierte/wiederkehrende Restore-Tests**: diese Session liefert ein Skript,
  das ein Mensch/CI manuell aufruft — keine geplante, wiederkehrende Ausführung.
- **`type="s3"`-Storage-Ziele**: erkannt, aber nicht gesichert/wiederhergestellt (siehe
  oben) — im aktuellen Stack ohnehin nicht aktiv konfiguriert.
