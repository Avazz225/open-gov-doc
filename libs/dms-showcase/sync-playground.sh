#!/usr/bin/env bash
# Mirrors every illustration in libs/dms-showcase into the stageshow playground
# (~/.claude/skills/stageshow/examples/og-<name>/) so it can be filmed with
# `?capture=og-<name>`. libs/dms-showcase stays the only source: edit here,
# re-run this, film there. Import paths are rewritten for the playground's
# layout (../kit -> ../../kit, ../look-ogdoc.css -> ./look-ogdoc.css).
#
#   bash libs/dms-showcase/sync-playground.sh          # sync all
#   bash libs/dms-showcase/sync-playground.sh --clean  # remove the og-* copies again
# (via bash: the repo does not track the executable bit, core.fileMode=false)
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
examples="${STAGESHOW_DIR:-$HOME/.claude/skills/stageshow}/examples"

if [[ "${1:-}" == "--clean" ]]; then
  rm -rf "$examples"/og-*
  echo "removed playground copies"
  exit 0
fi

for dir in "$here"/*-login; do
  [[ -d "$dir" ]] || continue
  name="og-$(basename "$dir" -login)"
  target="$examples/$name"
  rm -rf "$target"
  mkdir -p "$target"
  cp "$here/look-ogdoc.css" "$target/"
  for f in "$dir"/*.css; do cp "$f" "$target/"; done
  tsx=("$dir"/*.tsx)
  sed -e "s#'\.\./kit'#'../../kit'#" \
      -e "s#'\.\./kit/stageshow\.css'#'../../kit/stageshow.css'#" \
      -e "s#'\.\./look-ogdoc\.css'#'./look-ogdoc.css'#" \
      "${tsx[0]}" > "$target/index.tsx"
  echo "$name <- $(basename "$dir")"
done
