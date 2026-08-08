#!/usr/bin/env bash
# Backup-Orchestrator (Konzept 10.4, P11-S3) - koordinierte Sicherung der
# Shared DB (per WAL-Archivierung, siehe infra/docker-compose.yml, +
# pg_basebackup) und der Storage-Backend-Ziele (separates Backup-Ziel
# außerhalb des regulären Zielsatzes, P11-S0-Entscheidung).
#
# Bewusst als operatives Skript, kein dauerhaft laufender Service (Rückfrage
# bei Sessionstart) - gleiches Muster wie scripts/rolling-update.sh: kein
# Container mit stehenden privilegierten Postgres-/Storage-Credentials.
#
# Ablauf:
#   1. Storage-Zielkonfiguration aus dem laufenden storage-service-Container
#      lesen (DMS_TARGETS-Env-Var), jedes Ziel mit role != "archive" sichern
#      (type=local: `docker exec ... tar` des Backend-Wurzelverzeichnisses;
#      andere Typen werden übersprungen und als Lücke gemeldet, siehe
#      docs/operations/backup-restore.md - im aktuellen Stack ist ohnehin nur
#      type=local aktiv).
#   2. `pg_basebackup` gegen den laufenden Postgres-Container (online/hot -
#      kein Wartungsmodus nötig, das ist der ganze Sinn eines WAL-basierten
#      Backups: die Konsistenz entsteht durch WAL-Replay bei der
#      Wiederherstellung, nicht durch Einfrieren der Schreibzugriffe).
#   3. Aktuelle WAL-LSN notieren - zusammen mit den Zeitstempeln der
#      Storage-Sicherung der "Konsistenzanker" aus Konzept 10.4 ("Storage-
#      Backup und Datenbank-Backup werden auf einen gemeinsamen,
#      koordinierten Zeitpunkt festgelegt").
#   4. manifest.json schreiben - einzige Quelle der Wahrheit, welche
#      Artefakte zusammengehören.
#
# Konfiguration (7.3-Export) bewusst NICHT eigens gesichert: praktisch die
# gesamte Laufzeitkonfiguration liegt bereits in Postgres-Singleton-Tabellen
# und ist damit automatisch Teil der DB-Sicherung. Der vom Konzept gemeinte
# *unabhängige* Konfigurationsexport braucht 7.3 (P12-S3, existiert noch
# nicht) - manifest.json trägt dafür ein `config_export: null`-Platzhalterfeld.
#
# Usage:
#   scripts/backup.sh [--dest DIR]

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POSTGRES_CONTAINER="dms-postgres-1"
STORAGE_CONTAINER="dms-storage-service-1"
POSTGRES_USER="${POSTGRES_USER:-dms}"
POSTGRES_DB="${POSTGRES_DB:-dms}"

DEST_ROOT="$REPO_ROOT/backups"

while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST_ROOT="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unbekanntes Argument: $1" >&2; exit 1 ;;
  esac
done

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$DEST_ROOT/$TIMESTAMP"
mkdir -p "$BACKUP_DIR"
echo "==> Backup-Verzeichnis: $BACKUP_DIR"

echo "==> Lese Storage-Zielkonfiguration aus '$STORAGE_CONTAINER'"
TARGETS_JSON="$(docker exec "$STORAGE_CONTAINER" printenv DMS_TARGETS)"
if [ -z "$TARGETS_JSON" ]; then
  echo "FEHLER: DMS_TARGETS konnte nicht aus '$STORAGE_CONTAINER' gelesen werden - läuft der Container?" >&2
  exit 1
fi

echo "==> Sichere Storage-Ziele (role=archive wird übersprungen - eigener Zweck, kein Backup-Ziel)"
BACKUP_DIR="$BACKUP_DIR" TARGETS_JSON="$TARGETS_JSON" STORAGE_CONTAINER="$STORAGE_CONTAINER" python3 <<'PYEOF'
import json
import os
import subprocess
import sys

targets = json.loads(os.environ["TARGETS_JSON"])
backup_dir = os.environ["BACKUP_DIR"]
container = os.environ["STORAGE_CONTAINER"]
saved = []
skipped = []

for t in targets:
    target_id = t["id"]
    if t.get("role") == "archive":
        print(f"    -> {target_id}: role=archive, übersprungen (Aussonderungsziel, kein Backup-Ziel)")
        continue
    if t["type"] != "local":
        print(f"    WARNUNG: Ziel '{target_id}' hat Typ '{t['type']}' - in dieser Session nicht", file=sys.stderr)
        print(f"    unterstützt (nur type=local implementiert/verifiziert), siehe docs/operations/backup-restore.md", file=sys.stderr)
        skipped.append({"id": target_id, "type": t["type"]})
        continue
    dest = os.path.join(backup_dir, f"storage-{target_id}.tar.gz")
    print(f"    -> {target_id} (local, {t['base_path']})")
    with open(dest, "wb") as f:
        result = subprocess.run(
            ["docker", "exec", container, "tar", "czf", "-", "-C", t["base_path"], "."],
            stdout=f,
        )
    if result.returncode != 0:
        print(f"FEHLER: Sicherung von Ziel '{target_id}' fehlgeschlagen", file=sys.stderr)
        sys.exit(1)
    saved.append({"id": target_id, "type": t["type"], "base_path": t["base_path"], "archive": os.path.basename(dest)})

with open(os.path.join(backup_dir, ".storage_targets.json"), "w", encoding="utf-8") as f:
    json.dump({"saved": saved, "skipped": skipped}, f, indent=2)
PYEOF
if [ $? -ne 0 ]; then
  echo "FEHLER: Storage-Sicherung fehlgeschlagen - Backup unvollständig, breche ab." >&2
  exit 1
fi

echo "==> Sichere Datenbank (pg_basebackup, online/hot - kein Wartungsmodus nötig)"
docker exec "$POSTGRES_CONTAINER" rm -rf /tmp/dms-basebackup
docker exec "$POSTGRES_CONTAINER" pg_basebackup -U "$POSTGRES_USER" -D /tmp/dms-basebackup -Fp -Xs -P
if [ $? -ne 0 ]; then
  echo "FEHLER: pg_basebackup fehlgeschlagen - Backup unvollständig, breche ab." >&2
  docker exec "$POSTGRES_CONTAINER" rm -rf /tmp/dms-basebackup
  exit 1
fi

echo "==> Notiere aktuelle WAL-LSN (Konsistenzanker)"
WAL_LSN="$(docker exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT pg_current_wal_lsn();")"
BACKUP_COMPLETED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "==> Kopiere Basebackup auf den Host"
docker cp "$POSTGRES_CONTAINER:/tmp/dms-basebackup" "$BACKUP_DIR/db-basebackup"
docker exec "$POSTGRES_CONTAINER" rm -rf /tmp/dms-basebackup

WAL_VOLUME="$(docker inspect "$POSTGRES_CONTAINER" --format '{{ range .Mounts }}{{ if eq .Destination "/wal-archive" }}{{ .Name }}{{ end }}{{ end }}')"

echo "==> Schreibe manifest.json"
BACKUP_DIR="$BACKUP_DIR" BACKUP_COMPLETED_AT="$BACKUP_COMPLETED_AT" WAL_LSN="$WAL_LSN" \
WAL_VOLUME="$WAL_VOLUME" python3 <<'PYEOF'
import json
import os

backup_dir = os.environ["BACKUP_DIR"]
with open(os.path.join(backup_dir, ".storage_targets.json"), encoding="utf-8") as f:
    storage_targets = json.load(f)
os.remove(os.path.join(backup_dir, ".storage_targets.json"))

manifest = {
    "created_at": os.environ["BACKUP_COMPLETED_AT"],
    "wal_lsn_at_backup": os.environ["WAL_LSN"],
    "wal_archive_volume": os.environ["WAL_VOLUME"],
    "db_basebackup_dir": "db-basebackup",
    "storage_targets": storage_targets,
    # 7.3 (Konfigurationsexport) existiert noch nicht (P12-S3) - Platzhalter,
    # siehe docs/operations/backup-restore.md.
    "config_export": None,
}
with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
print(json.dumps(manifest, indent=2))
PYEOF

echo "==> Backup abgeschlossen: $BACKUP_DIR"
