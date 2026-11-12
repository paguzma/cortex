#!/usr/bin/env bash
# Construye la imagen, la levanta con un /data vacío y corre la prueba de punta a punta.
set -euo pipefail
cd "$(dirname "$0")/../.."

NAME="cortex-e2e-$$"
PORT="${CORTEX_E2E_PORT:-18700}"

docker build -q -t cortex:e2e . >/dev/null
docker volume create "$NAME" >/dev/null

cleanup() {
  if [ "${KEEP_LOGS:-0}" = "1" ]; then docker logs "$NAME" 2>&1 | tail -80 || true; fi
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker volume rm "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$NAME" -p "$PORT:8700" -v "$NAME:/data" cortex:e2e >/dev/null

CODE=""
for _ in $(seq 1 150); do
  CODE=$(docker logs "$NAME" 2>&1 | sed -n 's/.*Setup code: \([A-Z0-9-]*\).*/\1/p' | tail -1)
  if [ -n "$CODE" ] && curl -fsS -o /dev/null "http://127.0.0.1:$PORT/setup/login" \
     && docker exec "$NAME" curl -s -o /dev/null http://127.0.0.1:8766/mcp; then
    break
  fi
  sleep 2
done
if [ -z "$CODE" ]; then
  echo "the setup code never showed up" >&2
  docker logs "$NAME" >&2
  exit 1
fi

CORTEX_E2E_URL="http://127.0.0.1:$PORT" CORTEX_E2E_CODE="$CODE" CORTEX_E2E_CONTAINER="$NAME" \
  uv run --extra dev pytest -q -o addopts="" tests/e2e
