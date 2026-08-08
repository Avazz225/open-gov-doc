#!/usr/bin/env bash
# Automatisierter, sich selbst pruefender Restore-Test (Konzept 10.4, P11-S4).
#
# Baut echt nach, was Konzept 10.4 als Gefahr beschreibt: ein Backup wird VOR
# einer spaeteren, echten Zwangsloeschung gezogen; ein Restore auf genau
# diesen Backup-Zeitpunkt wuerde das Dokument unbeabsichtigt "wiederaufleben"
# lassen. Prueft per Exit-Code (nicht nur Log-Ausgabe), dass
# scripts/restore.sh den Fall tatsaechlich erkennt.
#
# Rückfrage bei Sessionstart (geteilter Ansatz): die ERKENNUNG laeuft echt
# gegen die isolierte Scratch-DB aus P11-S3/restore.sh. Die tatsaechliche
# ERNEUTE Ausfuehrung der physischen Loeschung wird separat gegen ein
# zweites, eigenes Testdokument direkt am laufenden Live-Stack verifiziert
# (kein Schatten-Anwendungsstack fuer diese Session).
#
# Usage:
#   scripts/test-restore.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCUMENT_SERVICE_URL="${DOCUMENT_SERVICE_URL:-http://localhost:${DOCUMENT_SERVICE_PORT:-8006}}"
COMPOSE_FILE="$REPO_ROOT/infra/docker-compose.yml"

FAILURES=0
BACKUP_DIR=""
DOC_A=""
DOC_E=""

cleanup() {
  echo "==> Räume Testdaten auf"
  [ -n "$BACKUP_DIR" ] && rm -rf "$BACKUP_DIR"
  # DOC_A ist am Ende dieses Tests bereits zwangsgelöscht (das ist der
  # Testzweck) - nichts mehr aufzuräumen. DOC_E ebenso, sofern Teil 2
  # erfolgreich war; falls nicht, bleibt es als harmloser Testrest liegen
  # (gleiche Praxis wie in anderen Live-Verifikationen dieses Projekts).
}
trap cleanup EXIT

assert() {
  local description="$1"
  local condition="$2"
  if [ "$condition" = "true" ]; then
    echo "    OK: $description"
  else
    echo "    FEHLER: $description" >&2
    FAILURES=$((FAILURES + 1))
  fi
}

echo "=== Teil 1: Erkennung einer Resurrection gegen die Scratch-DB ==="

echo "==> Lege Testdokument A an (wird nach dem Backup zwangsgelöscht)"
DOC_A="$(curl -sf -X POST "$DOCUMENT_SERVICE_URL/documents" \
  -F "title=test-restore-doc-a" -F "created_by=test-restore" \
  -F "file=@/etc/hostname;filename=a.txt;type=text/plain" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")"
echo "    Dokument A: $DOC_A"

echo "==> Nehme Backup (Dokument A ist darin noch aktiv)"
BACKUP_OUTPUT="$("$REPO_ROOT/scripts/backup.sh" 2>&1)"
echo "$BACKUP_OUTPUT" | tail -5
BACKUP_DIR="$(echo "$BACKUP_OUTPUT" | grep "Backup abgeschlossen:" | sed 's/.*: //')"
if [ -z "$BACKUP_DIR" ] || [ ! -f "$BACKUP_DIR/manifest.json" ]; then
  echo "FEHLER: backup.sh hat kein gültiges Backup-Verzeichnis geliefert." >&2
  exit 1
fi
BACKUP_CREATED_AT="$(python3 -c "import json; print(json.load(open('$BACKUP_DIR/manifest.json'))['created_at'])")"
# manifest.json speichert ISO-8601 (.../"T".../"Z") - Postgres' recovery_target_time
# erwartet dagegen sein eigenes Format ("... +00"), siehe restore.sh --help.
RECOVERY_TARGET_TIME_PG="$(echo "$BACKUP_CREATED_AT" | sed -e 's/T/ /' -e 's/Z/+00/')"

echo "==> Erzwinge einen WAL-Segmentwechsel, damit der Backup-Zeitpunkt sicher archiviert ist"
docker exec dms-postgres-1 psql -U dms -d dms -tc "SELECT pg_switch_wal();" >/dev/null
sleep 3

echo "==> Löse eine echte Zwangslöschung von Dokument A aus (Retention + erzwungener Poll-Tick)"
# retention_until muss in der Vergangenheit liegen, sonst greift
# list_due_for_retention_action's "retention_until <= now"-Filter nicht -
# ein reales, beim Schreiben dieses Skripts entdecktes Detail.
curl -sf -X PUT "$DOCUMENT_SERVICE_URL/documents/$DOC_A/retention" \
  -H "Content-Type: application/json" \
  -d '{"retention_until": "2020-01-01T00:00:00Z", "full_deletion": true, "reason": "test-restore"}' >/dev/null
docker compose -f "$COMPOSE_FILE" restart document-service >/dev/null

echo "==> Warte, bis Dokument A tatsächlich zwangsgelöscht ist (Timeout 60s)"
DELETED=0
for _ in $(seq 1 30); do
  status_code="$(curl -s -o /dev/null -w '%{http_code}' "$DOCUMENT_SERVICE_URL/documents/$DOC_A")"
  if [ "$status_code" = "404" ]; then
    DELETED=1
    break
  fi
  sleep 2
done
assert "Dokument A ist nach der Retention-Zwangslöschung tatsächlich weg (404)" "$([ "$DELETED" -eq 1 ] && echo true || echo false)"

echo "==> Erzwinge einen weiteren WAL-Segmentwechsel, damit die Löschung sicher archiviert ist"
docker exec dms-postgres-1 psql -U dms -d dms -tc "SELECT pg_switch_wal();" >/dev/null
sleep 3

echo "==> Führe restore.sh mit einem Zielzeitpunkt VOR der Zwangslöschung aus (Resurrection-Szenario)"
RESTORE_OUTPUT="$("$REPO_ROOT/scripts/restore.sh" "$BACKUP_DIR" --recovery-target-time "$RECOVERY_TARGET_TIME_PG" 2>&1)"
echo "$RESTORE_OUTPUT" | tail -25

RECONCILIATION_COUNT="$(echo "$RESTORE_OUTPUT" | grep -o 'RECONCILIATION_NEEDED_COUNT=[0-9]*' | tail -1 | cut -d= -f2)"
assert "restore.sh meldet mindestens 1 nötige Reconciliation" "$([ "${RECONCILIATION_COUNT:-0}" -ge 1 ] && echo true || echo false)"
assert "restore.sh nennt Dokument A explizit als Reconciliation-Fall" "$(echo "$RESTORE_OUTPUT" | grep -q "$DOC_A" && echo true || echo false)"

echo
echo "=== Teil 2: echte erneute physische Löschung gegen den Live-Stack ==="

echo "==> Lege Testdokument E an (bleibt aktiv, wird direkt über den Reconcile-Endpunkt gelöscht)"
DOC_E="$(curl -sf -X POST "$DOCUMENT_SERVICE_URL/documents" \
  -F "title=test-restore-doc-e" -F "created_by=test-restore" \
  -F "file=@/etc/hostname;filename=e.txt;type=text/plain" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")"
echo "    Dokument E: $DOC_E"

echo "==> Ohne Rolle: erwarte 403"
STATUS_NO_ROLE="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  "$DOCUMENT_SERVICE_URL/documents/$DOC_E/reconcile-restore-deletion" \
  -H "Content-Type: application/json" \
  -d '{"original_entry_id": "test-restore-ledger-id", "reason": "test-restore"}')"
assert "Reconcile-Endpunkt ohne Rolle liefert 403" "$([ "$STATUS_NO_ROLE" = "403" ] && echo true || echo false)"

echo "==> Mit dms-admin-Rolle: erwarte 204 und echte physische Löschung"
STATUS_WITH_ROLE="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  "$DOCUMENT_SERVICE_URL/documents/$DOC_E/reconcile-restore-deletion" \
  -H "Content-Type: application/json" -H "X-DMS-Roles: dms-admin" \
  -d '{"original_entry_id": "test-restore-ledger-id", "reason": "test-restore"}')"
assert "Reconcile-Endpunkt mit Rolle liefert 204" "$([ "$STATUS_WITH_ROLE" = "204" ] && echo true || echo false)"

STATUS_AFTER="$(curl -s -o /dev/null -w '%{http_code}' "$DOCUMENT_SERVICE_URL/documents/$DOC_E")"
assert "Dokument E ist danach real weg (404)" "$([ "$STATUS_AFTER" = "404" ] && echo true || echo false)"

REGISTER_ENTRY="$(curl -sf "$DOCUMENT_SERVICE_URL/deletion-register?document_id=$DOC_E" | python3 -c "
import json, sys
entries = json.load(sys.stdin)
print(entries[0]['triggered_by'] if entries else '')
")"
assert "DeletionRegisterEntry trägt triggered_by=system:restore-reconciliation" \
  "$([ "$REGISTER_ENTRY" = "system:restore-reconciliation" ] && echo true || echo false)"

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "=== ALLE ASSERTIONS ERFOLGREICH ==="
  exit 0
else
  echo "=== $FAILURES ASSERTION(EN) FEHLGESCHLAGEN ===" >&2
  exit 1
fi
