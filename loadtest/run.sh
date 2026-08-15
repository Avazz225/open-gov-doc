#!/usr/bin/env bash
# Runs the k6 mixed-usage load test against the local DMS stack, emitting
# results to two places at once:
#   - Prometheus remote-write, for live viewing in Grafana while the test runs
#   - a timestamped JSON file, for the loadtest/notebook/ analysis notebook
#     (aggregate summaries alone can't show the response-time trend across
#     the ramp, so JSON's per-sample output is what the notebook reads)
#
# Requires the Prometheus service to be started with
# --web.enable-remote-write-receiver (see infra/docker-compose.yml).
#
# Usage:
#   loadtest/run.sh                 # full 20 minute ramp test
#   SLA_MS=500 loadtest/run.sh      # override the goodput SLA threshold
#   loadtest/run.sh --vus 5 --iterations 5   # extra args passed through to k6

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
K6_BIN="$REPO_ROOT/loadtest/.tools/k6-v2.2.0-linux-amd64/k6"
RESULTS_DIR="$REPO_ROOT/loadtest/results"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULT_FILE="$RESULTS_DIR/run-${TIMESTAMP}.json"

if [ ! -x "$K6_BIN" ]; then
  echo "k6 not found at $K6_BIN - run loadtest/install-k6.sh first" >&2
  exit 1
fi

mkdir -p "$RESULTS_DIR"

echo "Prometheus remote-write: http://localhost:9090/api/v1/write"
echo "JSON results: $RESULT_FILE"

K6_PROMETHEUS_RW_SERVER_URL="${K6_PROMETHEUS_RW_SERVER_URL:-http://localhost:9090/api/v1/write}" \
  "$K6_BIN" run \
  --out experimental-prometheus-rw \
  --out "json=${RESULT_FILE}" \
  "$REPO_ROOT/loadtest/k6/scenario.js" \
  "$@"

echo "Done. Results written to $RESULT_FILE"
