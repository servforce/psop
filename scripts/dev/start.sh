#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/dev/common.sh
source "${SCRIPT_DIR}/common.sh"
REPO_ROOT="$(resolve_repo_root)"

load_psop_env "${REPO_ROOT}"
derive_local_integration_defaults

echo "[start] server: http://${PSOP_SERVER_HOST}:${PSOP_SERVER_PORT}" >&2
echo "[start] worker: runtime/build-test/material pools" >&2
echo "[start] web:    http://${PSOP_WEB_HOST}:${PSOP_WEB_PORT}" >&2
echo "[start] api:    ${PSOP_WEB_API_BASE_URL}" >&2

export PSOP_RUNTIME_WORKER_EMBEDDED_ENABLED=false

bash "${SCRIPT_DIR}/run-server.sh" &
SERVER_PID=$!

PIDS=("${SERVER_PID}")
WORKER_ENABLED="${PSOP_RUNTIME_WORKER_ENABLED:-true}"
if [[ "${WORKER_ENABLED,,}" =~ ^(1|true|yes|on)$ ]]; then
  bash "${SCRIPT_DIR}/run-worker.sh" &
  WORKER_PID=$!
  PIDS+=("${WORKER_PID}")
else
  echo "[start] worker disabled by PSOP_RUNTIME_WORKER_ENABLED" >&2
fi

bash "${SCRIPT_DIR}/run-web.sh" &
WEB_PID=$!
PIDS+=("${WEB_PID}")

cleanup() {
  trap - EXIT INT TERM
  kill "${PIDS[@]}" 2>/dev/null || true
  wait "${PIDS[@]}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM
wait -n "${PIDS[@]}"
