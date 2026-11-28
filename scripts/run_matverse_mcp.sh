#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
if [ -z "${MATVERSE_WEB_ORIGINS:-}" ]; then
  echo "MATVERSE_WEB_ORIGINS is empty. Configure explicit HTTPS origins in your MCP client environment." >&2
fi
exec python3 -m app.matverse_mcp
