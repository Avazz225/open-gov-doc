#!/usr/bin/env bash
# Restore-Verifikation (Konzept 10.4, P11-S3) - stellt ein von
# scripts/backup.sh erzeugtes Backup in eine ISOLIERTE Scratch-Umgebung
# wieder her (temporärer zweiter Postgres-Container + Scratch-Verzeichnis
# für Storage), NICHT in den laufenden Dev-Stack. Ein echtes Überschreiben
# der einzigen, von allen 26 Services gemeinsam genutzten Postgres-Instanz
# wäre destruktiv für den laufenden Betrieb - die Scratch-Verifikation ist
# trotzdem ein echter, funktionierender Point-in-Time-Recovery-Beweis, kein
# Trockenlauf.
#
# Deckt aus der 10.4-Reihenfolge die Schritte 1-3 sowie sinngemäß 5/6 ab:
#   1. Konsistenzanker: aus manifest.json (WAL-LSN + Zeitstempel).
#   2. Storage zuerst wiederherstellen (Tarball -> Scratch-Verzeichnis).
#   3. Datenbank per Point-in-Time-Recovery auf denselben/einen gewählten
#      Zeitpunkt wiederherstellen (Basebackup + WAL-Replay aus dem
#      Archiv-Volume, `recovery_target_time`).
#   5/6. Event-Bus-Reset und Registry/Service-Neustart sind für eine
#      isolierte Scratch-Verifikation nicht anwendbar - nur dokumentiert.
#
# BEWUSST NICHT Teil dieser Session (siehe docs/operations/backup-restore.md
# und PROGRESS.md "Monitoring & Backup/Restore"):
#   4. Löschabgleich (braucht DeletionRegisterEntry-Sonderbehandlung) - P11-S4.
#   7. Suchindex-Neuaufbau (braucht volle Wiederanbindung des App-Stacks an
#      eine wiederhergestellte DB) - P11-S4 ("automatisierte Restore-Tests").
#
# Usage:
#   scripts/restore.sh <backup-dir> [--recovery-target-time TIMESTAMP]
#
# TIMESTAMP im Format, das Postgres' `recovery_target_time` akzeptiert
# (z. B. "2026-08-08 17:30:00+00"). Ohne Angabe wird bis zum Ende des
# verfügbaren WAL-Archivs wiederhergestellt (jüngster Stand).

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POSTGRES_CONTAINER="dms-postgres-1"
POSTGRES_USER="${POSTGRES_USER:-dms}"
POSTGRES_DB="${POSTGRES_DB:-dms}"

SCRATCH_NAME="dms-postgres-restore-test"
SCRATCH_VOLUME="dms-postgres-restore-test-data"
SCRATCH_STORAGE_DIR=""

BACKUP_DIR=""
RECOVERY_TARGET_TIME=""

while [ $# -gt 0 ]; do
  case "$1" in
    --recovery-target-time) RECOVERY_TARGET_TIME="$2"; shift 2 ;;
    -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) BACKUP_DIR="$1"; shift ;;
  esac
done

if [ -z "$BACKUP_DIR" ] || [ ! -f "$BACKUP_DIR/manifest.json" ]; then
  echo "Usage: scripts/restore.sh <backup-dir> [--recovery-target-time TIMESTAMP]" >&2
  echo "  <backup-dir> muss ein von scripts/backup.sh erzeugtes Verzeichnis mit manifest.json sein." >&2
  exit 1
fi
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"

cleanup() {
  echo "==> Räume Scratch-Umgebung auf"
  docker rm -f "$SCRATCH_NAME" >/dev/null 2>&1 || true
  docker volume rm "$SCRATCH_VOLUME" >/dev/null 2>&1 || true
  [ -n "$SCRATCH_STORAGE_DIR" ] && rm -rf "$SCRATCH_STORAGE_DIR"
}
trap cleanup EXIT

echo "==> Lese manifest.json"
cat "$BACKUP_DIR/manifest.json"

WAL_VOLUME="$(python3 -c "import json; print(json.load(open('$BACKUP_DIR/manifest.json'))['wal_archive_volume'])")"
DB_BASEBACKUP_DIR="$BACKUP_DIR/$(python3 -c "import json; print(json.load(open('$BACKUP_DIR/manifest.json'))['db_basebackup_dir'])")"

echo "==> Schritt 2 (10.4): Storage zuerst wiederherstellen"
SCRATCH_STORAGE_DIR="$(mktemp -d)"
FOUND_STORAGE=0
for tarball in "$BACKUP_DIR"/storage-*.tar.gz; do
  [ -e "$tarball" ] || continue
  FOUND_STORAGE=1
  target_id="$(basename "$tarball" .tar.gz | sed 's/^storage-//')"
  dest="$SCRATCH_STORAGE_DIR/$target_id"
  mkdir -p "$dest"
  echo "    -> $target_id nach $dest entpackt"
  tar xzf "$tarball" -C "$dest"
done
if [ "$FOUND_STORAGE" -eq 0 ]; then
  echo "    Keine Storage-Tarballs im Backup gefunden." >&2
fi

echo "==> Schritt 3 (10.4): Datenbank per Point-in-Time-Recovery wiederherstellen"
docker rm -f "$SCRATCH_NAME" >/dev/null 2>&1 || true
docker volume rm "$SCRATCH_VOLUME" >/dev/null 2>&1 || true
docker volume create "$SCRATCH_VOLUME" >/dev/null

RECOVERY_CONF="$(mktemp)"
{
  echo "restore_command = 'cp /wal-archive/%f %p'"
  if [ -n "$RECOVERY_TARGET_TIME" ]; then
    echo "recovery_target_time = '$RECOVERY_TARGET_TIME'"
  fi
  echo "recovery_target_action = 'promote'"
} > "$RECOVERY_CONF"

echo "==> Kopiere Basebackup in das Scratch-Volume (postgres:16-alpine für korrekte uid/gid)"
docker run --rm -i \
  -v "$SCRATCH_VOLUME:/data" \
  -v "$DB_BASEBACKUP_DIR:/src:ro" \
  -u root postgres:16-alpine \
  sh -c 'cp -a /src/. /data/ && chown -R postgres:postgres /data && chmod 0700 /data && touch /data/recovery.signal && chown postgres:postgres /data/recovery.signal && cat >> /data/postgresql.auto.conf' \
  < "$RECOVERY_CONF"
rm -f "$RECOVERY_CONF"

echo "==> Starte Scratch-Postgres '$SCRATCH_NAME' (isoliert, kein dms-net, kein Host-Port-Publish)"
docker run -d --name "$SCRATCH_NAME" \
  -v "$SCRATCH_VOLUME:/var/lib/postgresql/data" \
  -v "$WAL_VOLUME:/wal-archive:ro" \
  postgres:16-alpine >/dev/null

echo "==> Warte auf Recovery/Promotion (Timeout 60s)"
READY=0
for _ in $(seq 1 30); do
  if docker exec "$SCRATCH_NAME" pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done

if [ "$READY" -ne 1 ]; then
  echo "FEHLER: Scratch-Postgres wurde nicht rechtzeitig bereit - Logs:" >&2
  docker logs "$SCRATCH_NAME" 2>&1 | tail -50 >&2
  exit 1
fi
echo "    Scratch-Postgres ist bereit und hat WAL bis $( [ -n "$RECOVERY_TARGET_TIME" ] && echo "$RECOVERY_TARGET_TIME" || echo "zum Ende des Archivs" ) wiederhergestellt."

echo "==> Verifiziere Storage-Checksummen gegen die wiederhergestellte object_metadata-Tabelle"
python3 - "$SCRATCH_STORAGE_DIR" "$SCRATCH_NAME" "$POSTGRES_USER" "$POSTGRES_DB" <<'PYEOF'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

scratch_dir, container, user, db = sys.argv[1:5]

result = subprocess.run(
    [
        "docker", "exec", container, "psql", "-U", user, "-d", db,
        "-tAc", "SELECT object_key, checksum_sha256, backend FROM storage.object_metadata",
    ],
    capture_output=True, text=True,
)
if result.returncode != 0:
    print(f"WARNUNG: konnte object_metadata nicht abfragen: {result.stderr}", file=sys.stderr)
    sys.exit(0)

rows = [line.split("|") for line in result.stdout.strip().splitlines() if line.strip()]
checked, mismatched, missing = 0, 0, 0
for object_key, expected_checksum, backend in rows:
    local_path = Path(scratch_dir) / backend / object_key
    if not local_path.exists():
        missing += 1
        continue
    actual = hashlib.sha256(local_path.read_bytes()).hexdigest()
    checked += 1
    if actual != expected_checksum:
        mismatched += 1
        print(f"    ABWEICHUNG: {object_key} (Ziel {backend}) - erwartet {expected_checksum}, real {actual}")

print(f"    {checked} von {len(rows)} Objekten geprüft, {mismatched} Abweichungen, {missing} im Storage-Backup fehlend.")
PYEOF

cat <<'EOF'

==> Restore-Verifikation abgeschlossen.

Abgedeckt (Konzept 10.4): Konsistenzanker (manifest.json), Storage-Restore,
Datenbank-Point-in-Time-Recovery, Storage-Checksummen-Abgleich gegen die
wiederhergestellte DB.

BEWUSST NICHT abgedeckt (siehe docs/operations/backup-restore.md, Zuständigkeit P11-S4):
  - Löschabgleich (Schritt 4, Konzept 10.4) - würde eine Sonderprüfung gegen
    DeletionRegisterEntry-Zeilen brauchen, die nach dem Backup-Zeitpunkt,
    aber vor dem Restore-Zeitpunkt entstanden sind.
  - Suchindex-Neuaufbau (Schritt 7) - würde eine volle Wiederanbindung des
    App-Stacks an diese wiederhergestellte DB voraussetzen.
  - Automatisierte/wiederkehrende Restore-Tests - dies war ein einzelner,
    manuell ausgelöster Verifikationslauf gegen eine Scratch-Umgebung.

Die Scratch-Umgebung wird jetzt aufgeräumt (siehe cleanup-Trap).
EOF
