#!/bin/sh
set -eu
BASE_URL="${GPB_API_URL:-http://127.0.0.1:8787}"
TOKEN="${GPB_API_TOKEN:?GPB_API_TOKEN is required}"
WEB_URL="${GPB_WEB_URL:-http://127.0.0.1:3000}"

curl -fsS "$BASE_URL/health" | grep -q '"status":"ok"'
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/stats" | grep -q '"stats"'
curl -fsS -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  "$BASE_URL/mcp" | grep -q 'gpt-project-bridge'
curl -fsS "$WEB_URL/" | grep -q 'GPT Project Bridge'
echo 'smoke test passed'
