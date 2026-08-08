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
# Deckt aus der 10.4-Reihenfolge die Schritte 1-4 sowie sinngemäß 5/6 ab:
#   1. Konsistenzanker: aus manifest.json (WAL-LSN + Zeitstempel).
#   2. Storage zuerst wiederherstellen (Tarball -> Scratch-Verzeichnis).
#   3. Datenbank per Point-in-Time-Recovery auf denselben/einen gewählten
#      Zeitpunkt wiederherstellen (Basebackup + WAL-Replay aus dem
#      Archiv-Volume, `recovery_target_time`).
#   4. Löschabgleich (P11-S4): liest das vom audit-service unabhängig vom
#      DB-Restore gepflegte Löschregister-Ledger, filtert Einträge zwischen
#      Backup-Zeitpunkt und jetzt, prüft je Eintrag gegen die wiederher-
#      gestellte Scratch-DB, ob das Objekt dort "wiederauferstanden" ist -
#      meldet Treffer inkl. des Aufrufs, den ein Betreiber nach der Cutover-
#      Freigabe gegen das dann live angebundene System ausführen müsste
#      (die tatsächliche erneute Löschung braucht einen laufenden document-/
#      folder-service, den es in dieser isolierten Scratch-Umgebung bewusst
#      nicht gibt, siehe docs/operations/backup-restore.md).
#   5/6. Event-Bus-Reset und Registry/Service-Neustart sind für eine
#      isolierte Scratch-Verifikation nicht anwendbar - nur dokumentiert.
#
# BEWUSST NICHT Teil dieser Session (siehe docs/operations/backup-restore.md
# und PROGRESS.md "Monitoring & Backup/Restore"):
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
AUDIT_CONTAINER="dms-audit-service-1"
POSTGRES_USER="${POSTGRES_USER:-dms}"
POSTGRES_DB="${POSTGRES_DB:-dms}"
DOCUMENT_SERVICE_URL="${DOCUMENT_SERVICE_URL:-http://localhost:${DOCUMENT_SERVICE_PORT:-8006}}"
FOLDER_SERVICE_URL="${FOLDER_SERVICE_URL:-http://localhost:${FOLDER_SERVICE_PORT:-8008}}"

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

echo "==> Schritt 4 (10.4): Löschabgleich - prüfe das Löschregister-Ledger gegen die wiederhergestellte DB"
BACKUP_CREATED_AT="$(python3 -c "import json; print(json.load(open('$BACKUP_DIR/manifest.json'))['created_at'])")"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
LEDGER_FILE="$(mktemp)"
docker exec "$AUDIT_CONTAINER" cat /deletion-ledger/deletion-register.jsonl > "$LEDGER_FILE" 2>/dev/null || true

BACKUP_CREATED_AT="$BACKUP_CREATED_AT" NOW="$NOW" SCRATCH_NAME="$SCRATCH_NAME" \
POSTGRES_USER="$POSTGRES_USER" POSTGRES_DB="$POSTGRES_DB" \
DOCUMENT_SERVICE_URL="$DOCUMENT_SERVICE_URL" FOLDER_SERVICE_URL="$FOLDER_SERVICE_URL" \
LEDGER_FILE="$LEDGER_FILE" \
python3 <<'PYEOF'
import json
import os
import subprocess
import sys

backup_created_at = os.environ["BACKUP_CREATED_AT"]
now = os.environ["NOW"]
container = os.environ["SCRATCH_NAME"]
user = os.environ["POSTGRES_USER"]
db = os.environ["POSTGRES_DB"]

# Datei statt direkter Shell-Interpolation (vorherige Fassung reichte den
# Ledger-Inhalt roh durch einen unquoted Heredoc - Reason-/Actor-Freitext
# koennte $()/Backticks enthalten und wuere von der Shell interpretiert,
# bevor Python ihn je sieht). Datei + quoted Heredoc umgehen das vollstaendig.
with open(os.environ["LEDGER_FILE"], encoding="utf-8") as f:
    ledger_content = f.read()
os.remove(os.environ["LEDGER_FILE"])
entries = [json.loads(line) for line in ledger_content.splitlines() if line.strip()]
# Konzept 10.4 wörtlich: Einträge "nach dem Backup-Zeitpunkt, aber vor dem
# Restore-Zeitpunkt" - hier: dem tatsächlichen, realen Moment dieses Laufs
# (nicht recovery_target_time, das ist der Zeitpunkt IN der Vergangenheit,
# auf den zurückgerollt wird - der Resurrection-Fall entsteht gerade daraus).
candidates = [e for e in entries if backup_created_at < e["occurred_at"] < now]
print(f"    {len(entries)} Ledger-Eintraege gesamt, {len(candidates)} im Zeitfenster ({backup_created_at} .. {now}).")

needs_reconciliation = []
table_by_type = {"document": "document.document", "folder": "folder.folder"}
url_by_type = {
    "document": f"{os.environ['DOCUMENT_SERVICE_URL']}/documents",
    "folder": f"{os.environ['FOLDER_SERVICE_URL']}/folders",
}
for entry in candidates:
    table = table_by_type.get(entry["object_type"])
    if table is None:
        continue
    result = subprocess.run(
        ["docker", "exec", container, "psql", "-U", user, "-d", db,
         "-tAc", f"SELECT 1 FROM {table} WHERE id = '{entry['object_id']}'"],
        capture_output=True, text=True,
    )
    if result.stdout.strip() == "1":
        needs_reconciliation.append(entry)

if needs_reconciliation:
    print(f"    RECONCILIATION NOETIG fuer {len(needs_reconciliation)} Objekt(e):")
    for entry in needs_reconciliation:
        url = f"{url_by_type[entry['object_type']]}/{entry['object_id']}/reconcile-restore-deletion"
        print(f"      - {entry['object_type']} {entry['object_id']} "
              f"(urspruenglich geloescht {entry['occurred_at']}, entry_id={entry['entry_id']})")
        print(f"        Nach Cutover auf das restaurierte Live-System auszufuehren:")
        print(f"        curl -X POST {url} -H 'X-DMS-Roles: dms-admin' -H 'Content-Type: application/json' "
              f"-d '{{\"original_entry_id\": \"{entry['entry_id']}\", \"reason\": \"Restore-Abgleich\"}}'")
    print(f"RECONCILIATION_NEEDED_COUNT={len(needs_reconciliation)}")
else:
    print("    Keine Reconciliation noetig - keine der wiederhergestellten Objekte war laut Ledger zwischenzeitlich zwangsgeloescht.")
    print("RECONCILIATION_NEEDED_COUNT=0")
PYEOF

cat <<'EOF'

==> Restore-Verifikation abgeschlossen.

Abgedeckt (Konzept 10.4): Konsistenzanker (manifest.json), Storage-Restore,
Datenbank-Point-in-Time-Recovery, Storage-Checksummen-Abgleich, Löschabgleich
gegen das unabhängig gepflegte Löschregister-Ledger.

BEWUSST NICHT abgedeckt (siehe docs/operations/backup-restore.md, Zuständigkeit P11-S4):
  - Tatsächliche erneute Ausführung einer nötigen Reconciliation - braucht
    ein laufendes document-/folder-service gegen die wiederhergestellte DB,
    das es in dieser isolierten Scratch-Umgebung bewusst nicht gibt. Der
    exakte Aufruf für das dann live angebundene System wird oben ausgegeben.
  - Suchindex-Neuaufbau (Schritt 7) - würde eine volle Wiederanbindung des
    App-Stacks an diese wiederhergestellte DB voraussetzen.
  - Automatisierte/wiederkehrende Restore-Tests - dies war ein einzelner,
    manuell ausgelöster Verifikationslauf gegen eine Scratch-Umgebung
    (siehe aber scripts/test-restore.sh für einen sich selbst prüfenden Lauf).

Die Scratch-Umgebung wird jetzt aufgeräumt (siehe cleanup-Trap).
EOF
