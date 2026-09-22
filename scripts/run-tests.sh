#!/usr/bin/env bash
# Koordiniert Testläufe über alle Services hinweg und fasst sie zusammen.
#
# Löst zwei wiederkehrende manuelle Schritte ab (siehe PROGRESS.md "Tooling &
# Testing"):
#   1. NATS-Durable-Konflikt: ein Service mit eigenem Konsumenten (durable=
#      <service-name>) kann nicht gleichzeitig als Docker-Container UND als
#      `pytest`-Lauf denselben Subject abonnieren. Dieses Skript stoppt vor
#      jedem betroffenen Service-Testlauf gezielt dessen eigenen Container
#      und startet ihn danach wieder - alle anderen Container (für
#      Cross-Service-HTTP-Aufrufe ohne Mocking) bleiben währenddessen live.
#   2. TEST_POSTGRES_DSN-Default zeigt auf die echte `dms`-Datenbank, nicht
#      auf `dms_test` - ohne explizites Überschreiben liefe jeder Testlauf
#      gegen die Entwicklungsdatenbank (siehe P5-S2-Datenverlust in
#      PROGRESS.md). Dieses Skript exportiert TEST_POSTGRES_DSN immer explizit
#      auf `dms_test` und legt die Datenbank bei Bedarf an.
#
# Usage:
#   scripts/run-tests.sh                    # alle Services mit tests/
#   scripts/run-tests.sh document-service permission-service
#   scripts/run-tests.sh --build             # Images vor dem Lauf neu bauen
#   scripts/run-tests.sh --no-ruff           # nur pytest, kein ruff
#   scripts/run-tests.sh --down              # Stack am Ende stoppen (docker compose down)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/infra/docker-compose.yml"
LOG_DIR="$REPO_ROOT/.test-results"
POSTGRES_CONTAINER="dms-postgres-1"
TEST_DB="dms_test"

# Services mit eigenem NATS-Konsumenten (durable=<service-name>), DEREN
# TESTS EIGENSTÄNDIG PER IN-PROZESS-TESTCLIENT LAUFEN - siehe
# `grep -rl "durable=" services/*/src/*/*.py`. Nur diese müssen vor ihrem
# eigenen Testlauf als Container gestoppt werden, da sonst der laufende
# Container-Konsument und der in-process gestartete Test-Konsument um
# denselben Durable-Namen konkurrieren. Services, deren Tests stattdessen
# gegen den echten laufenden Container gehen (kein In-Prozess-`TestClient`,
# z. B. `config-service`/`migration-service`) gehören NICHT hierher, obwohl
# sie ebenfalls einen eigenen Konsumenten haben - ihre Tests brauchen den
# Container gerade laufend, ein Stoppen bricht sie (P17-S3-Fund: fälschlich
# hinzugefügtes `config-service` ließ jeden `config-service`-Test mit
# "Connection refused" fehlschlagen).
CONSUMER_SERVICES=(audit-service auth-service case-service document-service folder-service notification-service ocr-service permission-service query-service registry-service rendering-service reporting-service search-service teamspace-service workflow-service)

# Per-service Postgres role (Konzept 3.1, P68-S1) - "<role>:<dev-default-password>",
# same mapping as infra/postgres-init/002-service-roles.sh/docker-compose.yml's
# per-service DMS_POSTGRES_DSN, since a test run must exercise the SAME
# narrowed role production uses, not a superuser shortcut (the whole point of
# this session is proving the narrowing doesn't break anything - a test run
# against the superuser would prove nothing). The actual password can be
# overridden via the identical POSTGRES_PASSWORD_<SCHEMA> env var docker-
# compose.yml itself reads, so a non-default password stays in sync
# automatically. Services with no entry here (e.g. config-service, which has
# no Postgres schema at all) fall back to the superuser-based default DSN
# below, unchanged.
declare -A SERVICE_PG_ROLE=(
  [archival-service]="svc_archival:archival_dev_only"
  [audit-service]="svc_audit:audit_dev_only"
  [auth-service]="svc_auth:auth_dev_only"
  [case-service]="svc_case:case_dev_only"
  [document-service]="svc_document:document_dev_only"
  [favorite-service]="svc_favorite:favorite_dev_only"
  [federation-hub-service]="svc_federation:federation_dev_only"
  [fleet-management-service]="svc_fleet:fleet_dev_only"
  [folder-service]="svc_folder:folder_dev_only"
  [license-service]="svc_license:license_dev_only"
  [mail-connector]="svc_mail_connector:mail_connector_dev_only"
  [migration-service]="svc_migration:migration_dev_only"
  [monitoring-service]="svc_monitoring:monitoring_dev_only"
  [notification-service]="svc_notification:notification_dev_only"
  [object-type-service]="svc_object_type:object_type_dev_only"
  [ocr-service]="svc_ocr:ocr_dev_only"
  [permission-service]="svc_permission:permission_dev_only"
  [plugin-orchestration-service]="svc_orchestration:orchestration_dev_only"
  [query-service]="svc_query:query_dev_only"
  [registry-service]="svc_registry:registry_dev_only"
  [rendering-service]="svc_rendering:rendering_dev_only"
  [reporting-service]="svc_reporting:reporting_dev_only"
  [search-service]="svc_search:search_dev_only"
  [signature-service]="svc_signature:signature_dev_only"
  [storage-service]="svc_storage:storage_dev_only"
  [teamspace-service]="svc_teamspace:teamspace_dev_only"
  [virus-scan-service]="svc_virus_scan:virus_scan_dev_only"
  [workflow-service]="svc_workflow:workflow_dev_only"
)

BUILD=0
RUN_RUFF=1
TEARDOWN=0
SERVICES=()

for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    --no-ruff) RUN_RUFF=0 ;;
    --down) TEARDOWN=1 ;;
    -h|--help)
      sed -n '2,25p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) SERVICES+=("$arg") ;;
  esac
done

if [ ${#SERVICES[@]} -eq 0 ]; then
  for d in "$REPO_ROOT"/services/*/; do
    s="$(basename "$d")"
    [ -d "${d}tests" ] && SERVICES+=("$s")
  done
fi

mkdir -p "$LOG_DIR"
cd "$REPO_ROOT"

is_consumer_service() {
  local s="$1"
  for c in "${CONSUMER_SERVICES[@]}"; do
    [ "$c" = "$s" ] && return 0
  done
  return 1
}

is_running() {
  docker compose -f "$COMPOSE_FILE" ps --status running --services 2>/dev/null | grep -qx "$1"
}

echo "==> uv sync --all-packages"
uv sync --all-packages -q || { echo "uv sync fehlgeschlagen - abgebrochen." >&2; exit 1; }

echo "==> Docker-Stack sicherstellen (postgres, nats, alle Services für Cross-Service-Aufrufe)"
if [ "$BUILD" -eq 1 ]; then
  docker compose -f "$COMPOSE_FILE" up -d --build
else
  docker compose -f "$COMPOSE_FILE" up -d
fi

echo "==> Warte auf Postgres-Healthcheck"
for _ in $(seq 1 30); do
  docker exec "$POSTGRES_CONTAINER" pg_isready -U dms >/dev/null 2>&1 && break
  sleep 1
done

echo "==> Stelle sicher, dass '$TEST_DB' existiert"
docker exec "$POSTGRES_CONTAINER" psql -U dms -d dms -tc \
  "SELECT 1 FROM pg_database WHERE datname = '$TEST_DB'" | grep -q 1 || \
  docker exec "$POSTGRES_CONTAINER" psql -U dms -d dms -c "CREATE DATABASE $TEST_DB;"

# P68-S1: the per-service roles/passwords already exist cluster-wide (created
# once, at Postgres's first boot, by postgres-init/002-service-roles.sh) -
# but roles' SCHEMA OWNERSHIP is per-database, and that init script only ever
# ran against the `dms` database, never `dms_test` (which may not even have
# existed yet at that point). Idempotent - safe to re-run every time.
echo "==> Stelle Schema-Eigentümerschaft je Service in '$TEST_DB' sicher"
docker exec "$POSTGRES_CONTAINER" psql -U dms -d "$TEST_DB" -v ON_ERROR_STOP=1 -c \
  "CREATE SCHEMA IF NOT EXISTS archival AUTHORIZATION svc_archival; CREATE SCHEMA IF NOT EXISTS audit AUTHORIZATION svc_audit; CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION svc_auth; CREATE SCHEMA IF NOT EXISTS \"case\" AUTHORIZATION svc_case; CREATE SCHEMA IF NOT EXISTS document AUTHORIZATION svc_document; CREATE SCHEMA IF NOT EXISTS favorite AUTHORIZATION svc_favorite; CREATE SCHEMA IF NOT EXISTS federation AUTHORIZATION svc_federation; CREATE SCHEMA IF NOT EXISTS fleet AUTHORIZATION svc_fleet; CREATE SCHEMA IF NOT EXISTS folder AUTHORIZATION svc_folder; CREATE SCHEMA IF NOT EXISTS license AUTHORIZATION svc_license; CREATE SCHEMA IF NOT EXISTS mail_connector AUTHORIZATION svc_mail_connector; CREATE SCHEMA IF NOT EXISTS migration AUTHORIZATION svc_migration; CREATE SCHEMA IF NOT EXISTS monitoring AUTHORIZATION svc_monitoring; CREATE SCHEMA IF NOT EXISTS notification AUTHORIZATION svc_notification; CREATE SCHEMA IF NOT EXISTS object_type AUTHORIZATION svc_object_type; CREATE SCHEMA IF NOT EXISTS ocr AUTHORIZATION svc_ocr; CREATE SCHEMA IF NOT EXISTS permission AUTHORIZATION svc_permission; CREATE SCHEMA IF NOT EXISTS orchestration AUTHORIZATION svc_orchestration; CREATE SCHEMA IF NOT EXISTS query AUTHORIZATION svc_query; CREATE SCHEMA IF NOT EXISTS registry AUTHORIZATION svc_registry; CREATE SCHEMA IF NOT EXISTS rendering AUTHORIZATION svc_rendering; CREATE SCHEMA IF NOT EXISTS reporting AUTHORIZATION svc_reporting; CREATE SCHEMA IF NOT EXISTS search AUTHORIZATION svc_search; CREATE SCHEMA IF NOT EXISTS signature AUTHORIZATION svc_signature; CREATE SCHEMA IF NOT EXISTS storage AUTHORIZATION svc_storage; CREATE SCHEMA IF NOT EXISTS teamspace AUTHORIZATION svc_teamspace; CREATE SCHEMA IF NOT EXISTS virus_scan AUTHORIZATION svc_virus_scan; CREATE SCHEMA IF NOT EXISTS workflow AUTHORIZATION svc_workflow;"

# Every service's own test run still calls "CREATE SCHEMA IF NOT EXISTS
# <own>" itself (same conftest.py code path as production's main.py,
# unconditionally, no Alembic) - Postgres checks CREATE-ON-DATABASE
# privilege BEFORE checking whether the schema already exists ("IF NOT
# EXISTS" only suppresses the "already exists" error, confirmed live during
# this session), so every role needs it here too, not just on the
# ownership-transfer step above (which runs as the superuser, not needing
# this grant itself).
docker exec "$POSTGRES_CONTAINER" psql -U dms -d "$TEST_DB" -v ON_ERROR_STOP=1 -c \
  "GRANT CREATE ON DATABASE $TEST_DB TO svc_archival; GRANT CREATE ON DATABASE $TEST_DB TO svc_audit; GRANT CREATE ON DATABASE $TEST_DB TO svc_auth; GRANT CREATE ON DATABASE $TEST_DB TO svc_case; GRANT CREATE ON DATABASE $TEST_DB TO svc_document; GRANT CREATE ON DATABASE $TEST_DB TO svc_favorite; GRANT CREATE ON DATABASE $TEST_DB TO svc_federation; GRANT CREATE ON DATABASE $TEST_DB TO svc_fleet; GRANT CREATE ON DATABASE $TEST_DB TO svc_folder; GRANT CREATE ON DATABASE $TEST_DB TO svc_license; GRANT CREATE ON DATABASE $TEST_DB TO svc_mail_connector; GRANT CREATE ON DATABASE $TEST_DB TO svc_migration; GRANT CREATE ON DATABASE $TEST_DB TO svc_monitoring; GRANT CREATE ON DATABASE $TEST_DB TO svc_notification; GRANT CREATE ON DATABASE $TEST_DB TO svc_object_type; GRANT CREATE ON DATABASE $TEST_DB TO svc_ocr; GRANT CREATE ON DATABASE $TEST_DB TO svc_orchestration; GRANT CREATE ON DATABASE $TEST_DB TO svc_permission; GRANT CREATE ON DATABASE $TEST_DB TO svc_query; GRANT CREATE ON DATABASE $TEST_DB TO svc_registry; GRANT CREATE ON DATABASE $TEST_DB TO svc_rendering; GRANT CREATE ON DATABASE $TEST_DB TO svc_reporting; GRANT CREATE ON DATABASE $TEST_DB TO svc_search; GRANT CREATE ON DATABASE $TEST_DB TO svc_signature; GRANT CREATE ON DATABASE $TEST_DB TO svc_storage; GRANT CREATE ON DATABASE $TEST_DB TO svc_teamspace; GRANT CREATE ON DATABASE $TEST_DB TO svc_virus_scan; GRANT CREATE ON DATABASE $TEST_DB TO svc_workflow;"

export TEST_POSTGRES_DSN="postgresql+asyncpg://dms:dms_dev_only@localhost:5432/${TEST_DB}"
export TEST_NATS_URL="${TEST_NATS_URL:-nats://localhost:4222}"

declare -A RESULT_STATUS
declare -A RESULT_SUMMARY
declare -A RESULT_DURATION
OVERALL_OK=0

for service in "${SERVICES[@]}"; do
  if [ ! -d "$REPO_ROOT/services/$service/tests" ]; then
    echo "-- $service: kein tests/-Verzeichnis, übersprungen"
    continue
  fi

  STOPPED=0
  if is_consumer_service "$service" && is_running "$service"; then
    echo "==> Stoppe Container '$service' (eigener NATS-Konsument, Durable-Konflikt sonst)"
    docker compose -f "$COMPOSE_FILE" stop "$service" >/dev/null
    STOPPED=1
  fi

  # P68-S1: exercise the SAME narrowed per-service role production uses,
  # not the superuser default set above - falls through to that default for
  # any service with no Postgres schema of its own (e.g. config-service).
  if [ -n "${SERVICE_PG_ROLE[$service]:-}" ]; then
    role_and_pw="${SERVICE_PG_ROLE[$service]}"
    role="${role_and_pw%%:*}"
    default_pw="${role_and_pw#*:}"
    schema_env_var="POSTGRES_PASSWORD_$(echo "${role#svc_}" | tr '[:lower:]' '[:upper:]')"
    pw="${!schema_env_var:-$default_pw}"
    export TEST_POSTGRES_DSN="postgresql+asyncpg://${role}:${pw}@localhost:5432/${TEST_DB}"
  else
    export TEST_POSTGRES_DSN="postgresql+asyncpg://dms:dms_dev_only@localhost:5432/${TEST_DB}"
  fi

  echo "==> Teste $service"
  LOG_FILE="$LOG_DIR/${service}.log"
  START_TS=$(date +%s)
  if uv run pytest "services/$service/tests" -q --tb=short >"$LOG_FILE" 2>&1; then
    STATUS="OK"
  else
    STATUS="FAIL"
    OVERALL_OK=1
  fi
  END_TS=$(date +%s)
  RESULT_DURATION[$service]=$((END_TS - START_TS))

  SUMMARY_LINE=$(grep -E "^[0-9]+ (passed|failed|error)|passed|failed|error" "$LOG_FILE" | tail -1)
  RESULT_STATUS[$service]="$STATUS"
  RESULT_SUMMARY[$service]="${SUMMARY_LINE:-"(keine Zusammenfassung - siehe $LOG_FILE)"}"

  if [ "$STOPPED" -eq 1 ]; then
    echo "==> Starte Container '$service' wieder"
    docker compose -f "$COMPOSE_FILE" start "$service" >/dev/null
  fi
done

RUFF_STATUS="übersprungen"
if [ "$RUN_RUFF" -eq 1 ]; then
  echo "==> ruff check"
  if uv run ruff check . >"$LOG_DIR/ruff-check.log" 2>&1; then
    RUFF_CHECK_OK=1
  else
    RUFF_CHECK_OK=0
    OVERALL_OK=1
  fi
  echo "==> ruff format --check"
  if uv run ruff format --check . >"$LOG_DIR/ruff-format.log" 2>&1; then
    RUFF_FORMAT_OK=1
  else
    RUFF_FORMAT_OK=0
    OVERALL_OK=1
  fi
  if [ "$RUFF_CHECK_OK" -eq 1 ] && [ "$RUFF_FORMAT_OK" -eq 1 ]; then
    RUFF_STATUS="OK"
  else
    RUFF_STATUS="FAIL (siehe $LOG_DIR/ruff-check.log / ruff-format.log)"
  fi
fi

if [ "$TEARDOWN" -eq 1 ]; then
  echo "==> docker compose down"
  docker compose -f "$COMPOSE_FILE" down
fi

echo ""
echo "================= Testzusammenfassung ================="
printf "%-24s %-8s %-10s %s\n" "SERVICE" "STATUS" "DAUER" "ERGEBNIS"
for service in "${SERVICES[@]}"; do
  [ -z "${RESULT_STATUS[$service]:-}" ] && continue
  printf "%-24s %-8s %-10s %s\n" "$service" "${RESULT_STATUS[$service]}" "${RESULT_DURATION[$service]}s" "${RESULT_SUMMARY[$service]}"
done
echo "---------------------------------------------------------"
printf "%-24s %s\n" "ruff" "$RUFF_STATUS"
echo "==========================================================="
echo "Logs: $LOG_DIR/"

if [ "$OVERALL_OK" -eq 0 ]; then
  echo "Alles grün."
else
  echo "Mindestens ein Lauf ist fehlgeschlagen - siehe Logs oben."
fi

exit $OVERALL_OK
