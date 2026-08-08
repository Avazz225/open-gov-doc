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

# Services mit eigenem NATS-Konsumenten (durable=<service-name>) - siehe
# `grep -rl "durable=" services/*/src/*/*.py`. Nur diese müssen vor ihrem
# eigenen Testlauf als Container gestoppt werden.
CONSUMER_SERVICES=(audit-service auth-service case-service document-service folder-service notification-service ocr-service permission-service query-service registry-service rendering-service reporting-service search-service)

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
