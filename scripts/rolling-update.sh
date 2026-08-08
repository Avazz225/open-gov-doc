#!/usr/bin/env bash
# Unterbrechungsfreier Rolling Update fuer EINEN Service (Konzept 10.5),
# unter Wiederverwendung des in P10-S2 gebauten Drain-Mechanismus
# (registry-service: POST /instances/{id}/drain, seit P10-S3 zusaetzlich
# POST /instances/{id}/activate fuer Rollback).
#
# Es gibt in diesem Projekt keine Orchestrierungsplattform, die Rolling
# Updates automatisch ausfuehrt (P10-S0-Befund: nur Docker Compose als
# reales Deploy-Ziel) - dieses Skript uebernimmt die in 10.5 beschriebene
# Parallelbetrieb-Choreografie manuell mit Docker-Compose-Bordmitteln:
#   1. Bestehende Instanz-ID(s) des Service-Typs von der Registry abfragen.
#   2. Neues Image bauen.
#   3. Einen zweiten, temporaeren Container ("Canary") OHNE Host-Port-
#      Publish starten (kein Konflikt mit dem laufenden, port-gebundenen
#      Compose-Service) - eigener DNS-Name im dms-net, DMS_SELF_ADDRESS
#      entsprechend ueberschrieben.
#   4. Pollen, bis die Registry den Canary als neue, gesunde, aktive
#      Instanz meldet (Health-/Readiness-Check, exakt wie in 10.5
#      beschrieben - wiederverwendet die vorhandene Heartbeat-/healthy-
#      Berechnung, kein neues Protokoll).
#   5. Alte Instanz(en) draining setzen (nimmt keine neuen Anfragen mehr an,
#      laufende Vorgaenge laufen unangetastet weiter).
#   6. Gnadenfrist abwarten (kein generisches "laufende Vorgaenge
#      abgeschlossen"-Signal ueber beliebige Services hinweg vorhanden -
#      ehrlich dokumentierte Vereinfachung, siehe
#      docs/operations/rolling-updates.md).
#   7. Alte, reguläre Compose-Instanz stoppen/entfernen (SIGTERM -> die
#      Service-eigene Lifespan-Shutdown-Logik dereigistriert sich dabei
#      bereits selbst ueber dms-registry-client, kein expliziter DELETE-
#      Aufruf noetig).
#   8. Regulaeren, port-veroeffentlichten Container frisch starten (loest
#      den Canary endgueltig ab), dessen Bereitschaft ebenfalls abwarten.
#   9. Canary draining setzen, Gnadenfrist, stoppen/entfernen.
# Zu jedem Zeitpunkt bleibt mindestens eine gesunde, aktive Instanz
# erreichbar - kein Zeitfenster ganz ohne Bedienung.
#
# Rollback: schlaegt Schritt 4 fehl (neue Instanz wird nicht rechtzeitig
# gesund), bricht das Skript ab, OHNE die alte(n) Instanz(en) zu draining -
# nichts wurde umgeschaltet, der Canary wird aufgeraeumt. Ein Rollback NACH
# einem bereits erfolgten Drain ist manuell ueber
# POST /instances/{id}/activate moeglich, siehe
# docs/operations/rolling-updates.md.
#
# Usage:
#   scripts/rolling-update.sh <service>
#   scripts/rolling-update.sh <service> --drain-grace-seconds 45
#   scripts/rolling-update.sh <service> --readiness-timeout-seconds 90

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/infra/docker-compose.yml"
REGISTRY_URL="${REGISTRY_URL:-http://localhost:${REGISTRY_SERVICE_PORT:-8001}}"

# Gleiche Liste wie scripts/run-tests.sh's CONSUMER_SERVICES - Services mit
# eigenem exklusiven NATS-Durable-Consumer (durable=<service-name>). Ein
# zweiter, gleichzeitig laufender Container wuerde beim Abonnieren mit
# "consumer is already bound to a subscription" fehlschlagen - echter
# Parallelbetrieb braucht Queue-Groups statt exklusiver Durable-Namen, nicht
# Teil dieser Session (siehe docs/operations/rolling-updates.md).
CONSUMER_SERVICES=(audit-service auth-service case-service document-service folder-service notification-service ocr-service permission-service query-service registry-service rendering-service reporting-service search-service)

DRAIN_GRACE_SECONDS=30
READINESS_TIMEOUT_SECONDS=60
SERVICE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --drain-grace-seconds) DRAIN_GRACE_SECONDS="$2"; shift 2 ;;
    --readiness-timeout-seconds) READINESS_TIMEOUT_SECONDS="$2"; shift 2 ;;
    -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) SERVICE="$1"; shift ;;
  esac
done

if [ -z "$SERVICE" ]; then
  echo "Usage: scripts/rolling-update.sh <service> [--drain-grace-seconds N] [--readiness-timeout-seconds N]" >&2
  exit 1
fi

SERVICE_TYPE="$SERVICE"  # Konvention in diesem Projekt: service_type == Compose-Servicename

is_consumer_service() {
  local s="$1"
  for c in "${CONSUMER_SERVICES[@]}"; do
    [ "$c" = "$s" ] && return 0
  done
  return 1
}

if is_consumer_service "$SERVICE"; then
  cat >&2 <<EOF
Fehler: '$SERVICE' hat einen exklusiven NATS-Durable-Consumer (Durable-Name
= Servicename). Ein zweiter, gleichzeitig laufender Container wuerde beim
Abonnieren mit "consumer is already bound to a subscription" fehlschlagen -
dieselbe Einschraenkung, wegen der scripts/run-tests.sh betroffene Services
vor ihrem Testlauf gezielt stoppt. Echter Parallelbetrieb fuer
Consumer-Services braucht NATS-Queue-Groups statt exklusiver Durable-Namen -
nicht Teil dieser Session, siehe docs/operations/rolling-updates.md.
EOF
  exit 1
fi

get_instances_json() {
  curl -sf "$REGISTRY_URL/instances/$SERVICE_TYPE"
}

find_new_ready_instance() {
  local exclude_ids="$1"
  get_instances_json | EXCLUDE_IDS="$exclude_ids" python3 -c "
import json, os, sys
exclude = set(os.environ['EXCLUDE_IDS'].split())
for i in json.load(sys.stdin):
    if i['instance_id'] not in exclude and i.get('healthy') and i.get('status') == 'active':
        print(i['instance_id'])
        break
"
}

wait_for_new_instance() {
  local exclude_ids="$1"
  local found=""
  local deadline=$((SECONDS + READINESS_TIMEOUT_SECONDS))
  while [ "$SECONDS" -lt "$deadline" ]; do
    found="$(find_new_ready_instance "$exclude_ids")"
    [ -n "$found" ] && break
    sleep 2
  done
  echo "$found"
}

echo "==> Bestehende Instanzen von '$SERVICE_TYPE' abfragen"
OLD_IDS="$(get_instances_json | python3 -c "
import json, sys
print(' '.join(i['instance_id'] for i in json.load(sys.stdin)))
")"

if [ -z "$OLD_IDS" ]; then
  echo "==> Keine bestehende Instanz gefunden - normaler Erststart statt Rolling Update."
  docker compose -f "$COMPOSE_FILE" build "$SERVICE"
  docker compose -f "$COMPOSE_FILE" up -d "$SERVICE"
  exit 0
fi
echo "    Alte Instanz(en): $OLD_IDS"

echo "==> Baue neues Image"
docker compose -f "$COMPOSE_FILE" build "$SERVICE" || exit 1

CANARY_NAME="dms-${SERVICE}-canary"
echo "==> Starte temporären Canary-Container '$CANARY_NAME' (kein Host-Port-Publish)"
docker rm -f "$CANARY_NAME" >/dev/null 2>&1 || true
docker compose -f "$COMPOSE_FILE" run -d --no-deps --name "$CANARY_NAME" \
  -e "DMS_SELF_ADDRESS=http://${CANARY_NAME}:8000" \
  "$SERVICE" >/dev/null

echo "==> Warte auf Health-/Readiness-Check des Canary über die Registry (Timeout ${READINESS_TIMEOUT_SECONDS}s)"
CANARY_ID="$(wait_for_new_instance "$OLD_IDS")"
if [ -z "$CANARY_ID" ]; then
  echo "FEHLER: Canary wurde nicht rechtzeitig gesund - breche ab, alte Instanz(en) bleiben unangetastet (kein Drain ausgelöst)." >&2
  docker stop "$CANARY_NAME" >/dev/null 2>&1
  docker rm "$CANARY_NAME" >/dev/null 2>&1
  exit 1
fi
echo "    Canary ist bereit: $CANARY_ID"

echo "==> Draine alte Instanz(en)"
for id in $OLD_IDS; do
  echo "    POST /instances/$id/drain"
  curl -sf -X POST "$REGISTRY_URL/instances/$id/drain" >/dev/null
done

echo "==> Gnadenfrist ${DRAIN_GRACE_SECONDS}s, damit laufende Vorgänge auf den alten Instanzen abschließen können"
sleep "$DRAIN_GRACE_SECONDS"

echo "==> Stoppe die alte, reguläre Instanz von '$SERVICE'"
docker compose -f "$COMPOSE_FILE" stop "$SERVICE"
docker compose -f "$COMPOSE_FILE" rm -f "$SERVICE"

echo "==> Starte die neue, port-veröffentlichte Instanz von '$SERVICE'"
docker compose -f "$COMPOSE_FILE" up -d "$SERVICE"

echo "==> Warte auf Health-/Readiness-Check der regulären Instanz (Timeout ${READINESS_TIMEOUT_SECONDS}s)"
FINAL_ID="$(wait_for_new_instance "$OLD_IDS $CANARY_ID")"
if [ -z "$FINAL_ID" ]; then
  echo "FEHLER: die reguläre Instanz wurde nicht rechtzeitig gesund. Der Canary läuft noch und bedient weiterhin Anfragen - manuell prüfen: docker compose -f infra/docker-compose.yml logs $SERVICE" >&2
  exit 1
fi
echo "    Reguläre Instanz ist bereit: $FINAL_ID"

echo "==> Draine und entferne den Canary"
curl -sf -X POST "$REGISTRY_URL/instances/$CANARY_ID/drain" >/dev/null
sleep "$DRAIN_GRACE_SECONDS"
docker stop "$CANARY_NAME" >/dev/null
docker rm "$CANARY_NAME" >/dev/null

echo "==> Rolling Update von '$SERVICE' abgeschlossen (alte Instanz(en): $OLD_IDS -> neue Instanz: $FINAL_ID)."
