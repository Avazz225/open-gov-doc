#!/usr/bin/env bash
# Builds the admin skill (skills/og-doc-admin, ADR 0253) as a zip for a
# release: dist/og-doc-admin-skill-<version>.zip, the skill directory at its
# root so it can be unpacked into an agent's skills folder as is.
# Usage: scripts/package-skill.sh [version]   (default: git describe / date)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-$(git -C "$REPO_ROOT" describe --tags --always 2>/dev/null || date +%Y%m%d)}"
OUT="$REPO_ROOT/dist/og-doc-admin-skill-${VERSION}.zip"
mkdir -p "$REPO_ROOT/dist"
rm -f "$OUT"
(cd "$REPO_ROOT/skills" && python3 -c '
import sys, zipfile, pathlib
out = sys.argv[1]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for path in sorted(pathlib.Path("og-doc-admin").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            info = zipfile.ZipInfo.from_file(path, path.as_posix())
            # Scripts stay executable after unpacking.
            if path.suffix == ".sh":
                info.external_attr = 0o100755 << 16
            z.writestr(info, path.read_bytes())
' "$OUT")
echo "$OUT"
