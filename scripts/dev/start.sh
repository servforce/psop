#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "${SCRIPT_DIR}/run-server.sh" &
SERVER_PID=$!

bash "${SCRIPT_DIR}/run-web.sh" &
WEB_PID=$!

cleanup() {
  kill "${SERVER_PID}" "${WEB_PID}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM
wait "${SERVER_PID}" "${WEB_PID}"
