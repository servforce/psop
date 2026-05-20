#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/dev/common.sh
source "${SCRIPT_DIR}/common.sh"
REPO_ROOT="$(resolve_repo_root)"

load_psop_env "${REPO_ROOT}"
derive_local_integration_defaults

echo "[start] server: http://${PSOP_SERVER_HOST}:${PSOP_SERVER_PORT}" >&2
echo "[start] web:    http://${PSOP_WEB_HOST}:${PSOP_WEB_PORT}" >&2
echo "[start] api:    ${PSOP_WEB_API_BASE_URL}" >&2

bash "${SCRIPT_DIR}/run-server.sh" &
SERVER_PID=$!

bash "${SCRIPT_DIR}/run-web.sh" &
WEB_PID=$!

cleanup() {
  kill "${SERVER_PID}" "${WEB_PID}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM
wait "${SERVER_PID}" "${WEB_PID}"
