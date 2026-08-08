# Backup & Restore (Konzept 10.4, P11-S3/S4)

Konzept 10.4 verlangt koordinierte Sicherung von Shared DB, Storage-Backends und
Konfiguration, eine feste Wiederherstellungsreihenfolge (inkl. verpflichtendem
Löschabgleich) und einen Wartungsmodus während des Vorgangs. Diese Seite beschreibt den
real umgesetzten Mechanismus über beide Sessions hinweg.

## Architekturentscheidung bei Sessionstart

Zwei Rückfragen wurden vor der Umsetzung geklärt:

1. **Operative Skripte statt dauerhaft laufender Service**: `scripts/backup.sh`/
   `scripts/restore.sh`, kein neuer `backup-service` mit stehenden privilegierten
   Postgres-/Storage-Credentials — gleiches Muster wie `scripts/rolling-update.sh`
   (P10-S3) und die bewusste Docker-Socket-Abstinenz aus P10-S1.
2. **Scope-Abgrenzung P11-S3/P11-S4**: die Roadmap trennt bereits sauber zwischen dem
   Sicherungs-/Wiederherstellungs-*Mechanismus* (P11-S3) und dem **Löschabgleich nach
   Restore** + **automatisierten Restore-Tests** (P11-S4, siehe unten). P11-S3
   verifiziert den Mechanismus real, aber gegen eine isolierte Scratch-Umgebung — nicht
   gegen den laufenden Dev-Stack (die einzige gemeinsame Postgres-Instanz aller 26
   Services; ein echtes Überschreiben wäre destruktiv für die laufende Session).
3. **(P11-S4) Tiefe des automatisierten Restore-Tests**: geteilter Ansatz (Empfehlung)
   statt vollem Schatten-Anwendungsstack — siehe "Löschabgleich nach Restore" unten.

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

## Löschabgleich nach Restore (Schritt 4, Konzept 10.4, P11-S4)

Das Kernrisiko: ein Restore auf einen Zeitpunkt *vor* einer inzwischen durchgeführten
Zwangslöschung (5.2a) würde das eigentlich zu löschende Dokument/Objekt unbeabsichtigt
"wiederauferstehen" lassen.

- **Löschregister-Ledger, unabhängig vom DB-Restore-Zyklus**: `audit-service`s
  bestehender NATS-Consumer hängt bei jedem `document.force_deleted`/
  `folder.force_deleted`-Event eine Zeile an eine Append-only-Datei
  (`/deletion-ledger/deletion-register.jsonl`) auf einem **eigenen Docker-Volume**
  (`deletion-ledger-data`) an — technisch komplett getrennt von der Postgres-Instanz,
  die `backup.sh`/`restore.sh` sichern/wiederherstellen. Nur so bleibt das Register
  "auf dem aktuellsten Stand erhalten" (10.4 wörtlich), auch wenn die DB selbst auf
  einen älteren Zeitpunkt zurückgesetzt wird. Bewusst nur `forced_deletion`-Ereignisse
  (5.2a) — reguläre `trash_expiry`-Löschungen haben laut Konzept eine geringere
  Konsequenz und sind nicht Teil dieses Ledgers.
- **Erkennung, real gegen die Scratch-DB**: `scripts/restore.sh` liest nach dem
  Point-in-Time-Recovery das Ledger, filtert Einträge zwischen dem Backup-Zeitpunkt
  (`manifest.json`) und dem tatsächlichen, realen Moment des Restore-Laufs, und prüft
  je Eintrag per SQL-Abfrage gegen die wiederhergestellte Scratch-DB, ob das Objekt
  dort noch/wieder existiert. Treffer werden explizit gemeldet, inklusive des exakten
  `curl`-Aufrufs für die tatsächliche Wiederholung.
- **Erneute Ausführung, real gegen den Live-Stack**: `POST /documents/{id}/reconcile-restore-deletion`
  (document-service) und `POST /folders/{id}/reconcile-restore-deletion` (folder-service)
  — ruft "denselben Mechanismus wie bei der ursprünglichen Zwangslöschung" (10.4 wörtlich,
  `retention_actions.execute_forced_deletion()`) erneut auf, mit `triggered_by=
  "system:restore-reconciliation"` und einem zusätzlichen Event-Payload-Feld
  `reconciliation_of_entry_id`. Gate: `X-DMS-Roles` muss `dms-admin` enthalten.
- **Geteilter Testansatz** (Rückfrage bei Sessionstart): die *Erkennung* läuft
  vollständig automatisiert und real gegen die isolierte Scratch-Umgebung
  (`scripts/test-restore.sh`); die *tatsächliche erneute Löschung* wird separat und
  real gegen den laufenden Live-Stack verifiziert (Gate/Erfolg/Audit-Eintrag), statt
  einen kompletten zweiten Schatten-Anwendungsstack (document-service+storage-service
  gegen Scratch-Daten) aufzusetzen — beide Teilstücke sind echt getestet, nur nicht in
  einem einzigen durchgehenden Lauf verkettet.

## `scripts/test-restore.sh` (automatisierter Restore-Test, P11-S4)

```bash
scripts/test-restore.sh
```

Ein sich selbst prüfender, wiederholbarer Testlauf (Exit-Code, nicht nur Log-Ausgabe):
legt ein Testdokument an, nimmt ein Backup, löst danach eine echte Zwangslöschung aus
(Retention-Frist in die Vergangenheit gesetzt + erzwungener Poll-Tick per
Container-Neustart), restauriert auf einen Zeitpunkt *vor* dieser Löschung und prüft,
dass `restore.sh` den Fall real erkennt. Verifiziert danach separat den
Reconcile-Endpunkt gegen ein zweites, aktives Testdokument (Gate 403/204, echte
Löschung, echter Audit-Eintrag).

## Bewusst nicht Teil von P11-S3/S4

- **Suchindex-Neuaufbau (Schritt 7)**: würde eine volle Wiederanbindung des App-Stacks
  an eine wiederhergestellte DB voraussetzen (der Suchindex selbst braucht kein
  eigenes Backup, da rekonstruierbar) — offener Anschlusspunkt für eine mögliche
  spätere Session.
- **Tatsächliche automatische Ausführung der Reconciliation direkt aus `restore.sh`**:
  würde ein laufendes document-/folder-service gegen die wiederhergestellte DB
  voraussetzen, das es in der isolierten Scratch-Umgebung bewusst nicht gibt —
  `restore.sh` gibt stattdessen den exakten Aufruf für das Live-System aus.
- **Event-Bus-Reset (Schritt 5) / Registry-Neustart (Schritt 6)**: für eine isolierte
  Scratch-Verifikation nicht anwendbar (kein NATS/keine App-Services beteiligt) — nur
  in Konzept-Reihenfolge dokumentiert, nicht geskriptet.
- **Wartungsmodus während eines echten Produktions-Restores**: siehe Abschnitt oben —
  wird von einem echten Restore-Vorgang gebraucht, aber nicht von den
  Skript-gestützten Scratch-Verifikationen dieses Projekts.
- **Automatisierte/wiederkehrende (geplante/Cron-/CI-)Restore-Tests**: `test-restore.sh`
  ist ein wiederholbarer, sich selbst prüfender Lauf, aber ohne Cron-/CI-Einbindung in
  dieser Session.
- **`type="s3"`-Storage-Ziele**: erkannt, aber nicht gesichert/wiederhergestellt — im
  aktuellen Stack ohnehin nicht aktiv konfiguriert.
- **Reconciliation für `trash_expiry`-Löschungen und Aussonderung (5.6)**: Konzept 10.4
  nennt beides als analoge, aber weniger schwerwiegende Fälle - nicht Teil dieser Session.
