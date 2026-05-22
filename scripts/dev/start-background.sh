#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/dev/common.sh
source "${SCRIPT_DIR}/common.sh"
REPO_ROOT="$(resolve_repo_root)"

load_psop_env "${REPO_ROOT}"
derive_local_integration_defaults

LOG_DIR="${PSOP_DEV_LOG_DIR:-${REPO_ROOT}/logs}"
LOG_FILE="${PSOP_DEV_LOG_FILE:-${LOG_DIR}/dev-server.log}"
PID_FILE="${PSOP_DEV_PID_FILE:-${REPO_ROOT}/.dev-server.pid}"

mkdir -p "${LOG_DIR}"

stop_existing() {
  if [[ ! -f "${PID_FILE}" ]]; then
    return 0
  fi

  local old_pid
  old_pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -z "${old_pid}" || ! "${old_pid}" =~ ^[0-9]+$ ]]; then
    return 0
  fi
  if ! kill -0 "${old_pid}" 2>/dev/null; then
    return 0
  fi

  local cmdline
  cmdline="$(tr '\0' ' ' <"/proc/${old_pid}/cmdline" 2>/dev/null || true)"
  if [[ "${cmdline}" != *"scripts/dev/start.sh"* && "${cmdline}" != *"scripts/dev/start-background.sh"* ]]; then
    echo "[start-background] pid ${old_pid} is not a PSOP dev service, skip stop." >&2
    return 0
  fi

  kill -- -"${old_pid}" 2>/dev/null || kill "${old_pid}" 2>/dev/null || true
  sleep 1
  if kill -0 "${old_pid}" 2>/dev/null; then
    kill -9 -- -"${old_pid}" 2>/dev/null || kill -9 "${old_pid}" 2>/dev/null || true
  fi
}

stop_port_listener() {
  local port="${1}"
  if ! command -v fuser >/dev/null 2>&1; then
    return 0
  fi
  fuser -k -TERM "${port}/tcp" >/dev/null 2>&1 || true
  sleep 1
  fuser -k -KILL "${port}/tcp" >/dev/null 2>&1 || true
}

stop_existing
stop_port_listener "${PSOP_SERVER_PORT}"
stop_port_listener "${PSOP_WEB_PORT}"
: > "${LOG_FILE}"

setsid bash -lc 'cd "$1" && exec bash scripts/dev/start.sh' bash "${REPO_ROOT}" >"${LOG_FILE}" 2>&1 < /dev/null &
SERVICE_PID=$!
printf '%s\n' "${SERVICE_PID}" > "${PID_FILE}"

echo "[start-background] pid: ${SERVICE_PID}"
echo "[start-background] log: ${LOG_FILE}"
echo "[start-background] web: http://${PSOP_WEB_HOST}:${PSOP_WEB_PORT}"
echo "[start-background] api: ${PSOP_WEB_API_BASE_URL}"
