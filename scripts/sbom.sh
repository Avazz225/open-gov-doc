#!/usr/bin/env bash
# Software bill of materials (P94-S3): one CycloneDX 1.6 JSON per deployable
# unit - each Python service (its runtime dependencies from the uv lock,
# workspace libraries resolved to their third-party dependencies) and each
# Node app (production dependencies from its package-lock).
#
# Usage:
#   scripts/sbom.sh                 # everything into ./sbom/
#   scripts/sbom.sh --out DIR       # another output directory
#   scripts/sbom.sh --only python   # or: --only node
#
# Needs uv and npm/npx on PATH; the generators are fetched in pinned versions
# (uvx / npx), so nothing has to be installed globally.
set -euo pipefail

CYCLONEDX_PY="cyclonedx-bom==9.0.0"
CYCLONEDX_NPM="@cyclonedx/cyclonedx-npm@7.0.1"
SPEC_VERSION="1.6"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/sbom"
ONLY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$(realpath -m "$2")"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
failures=0

if [[ -z "$ONLY" || "$ONLY" == "python" ]]; then
  for dir in "$ROOT"/services/*/; do
    service="$(basename "$dir")"
    [[ -f "$dir/pyproject.toml" ]] || continue
    requirements="$TMP/$service.txt"
    if ! (cd "$ROOT" && uv export --package "$service" --format requirements-txt \
          --no-hashes --no-dev --no-emit-workspace -q >"$requirements"); then
      echo "FAIL  $service (uv export)" >&2; failures=$((failures + 1)); continue
    fi
    if uvx --python 3.12 --from "$CYCLONEDX_PY" cyclonedx-py requirements "$requirements" \
        --spec-version "$SPEC_VERSION" --output-format JSON \
        --output-file "$OUT/$service.cdx.json" >/dev/null 2>"$TMP/$service.err"; then
      echo "ok    $service"
    else
      echo "FAIL  $service (see below)" >&2; cat "$TMP/$service.err" >&2
      failures=$((failures + 1))
    fi
  done
fi

if [[ -z "$ONLY" || "$ONLY" == "node" ]]; then
  for dir in "$ROOT"/apps/*/; do
    app="$(basename "$dir")"
    [[ -f "$dir/package-lock.json" ]] || continue
    # cyclonedx-npm reads the installed tree; install exactly the lock if needed.
    if [[ ! -d "$dir/node_modules" ]]; then
      (cd "$dir" && npm ci --ignore-scripts --no-audit --no-fund >/dev/null)
    fi
    if (cd "$dir" && npx --yes "$CYCLONEDX_NPM" --omit dev --spec-version "$SPEC_VERSION" \
          --output-format JSON --output-file "$OUT/$app.cdx.json" >/dev/null 2>"$TMP/$app.err"); then
      echo "ok    $app"
    else
      echo "FAIL  $app (see below)" >&2; cat "$TMP/$app.err" >&2
      failures=$((failures + 1))
    fi
  done
fi

echo "SBOMs in $OUT"
exit $((failures > 0))
