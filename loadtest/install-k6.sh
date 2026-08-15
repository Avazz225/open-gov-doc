#!/usr/bin/env bash
# Downloads a pinned k6 release into loadtest/.tools/ (gitignored) so the
# load test can run without a system-wide k6 install or root access.
#
# Usage:
#   loadtest/install-k6.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$REPO_ROOT/loadtest/.tools"
K6_VERSION="v2.2.0"
K6_DIR="$TOOLS_DIR/k6-${K6_VERSION}-linux-amd64"
K6_BIN="$K6_DIR/k6"

if [ -x "$K6_BIN" ]; then
  echo "k6 ${K6_VERSION} already installed at $K6_BIN"
  exit 0
fi

mkdir -p "$TOOLS_DIR"
ARCHIVE="$TOOLS_DIR/k6.tar.gz"
URL="https://github.com/grafana/k6/releases/download/${K6_VERSION}/k6-${K6_VERSION}-linux-amd64.tar.gz"

echo "Downloading k6 ${K6_VERSION} from $URL"
curl -sL "$URL" -o "$ARCHIVE"
tar -xzf "$ARCHIVE" -C "$TOOLS_DIR"
rm -f "$ARCHIVE"
chmod +x "$K6_BIN"

echo "k6 installed at $K6_BIN"
"$K6_BIN" version
