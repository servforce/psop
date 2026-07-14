#!/usr/bin/env bash
set -euo pipefail

resolve_repo_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "${script_dir}/../.." && pwd
}

load_psop_env() {
  local repo_root="${1}"
  local env_file

  for env_file in "${repo_root}/.env" "${repo_root}/backend/.env"; do
    if [[ -f "${env_file}" ]]; then
      set -a
      # shellcheck disable=SC1090
      source "${env_file}"
      set +a
    fi
  done
}

derive_local_integration_defaults() {
  export PSOP_SERVER_HOST="${PSOP_SERVER_HOST:-0.0.0.0}"
  export PSOP_SERVER_PORT="${PSOP_SERVER_PORT:-8011}"
  export PSOP_WEB_HOST="${PSOP_WEB_HOST:-0.0.0.0}"
  export PSOP_WEB_PORT="${PSOP_WEB_PORT:-4173}"
  export PSOP_WEB_API_BASE_URL="${PSOP_WEB_API_BASE_URL:-http://${PSOP_SERVER_HOST}:${PSOP_SERVER_PORT}/api/v1}"

  export PSOP_CORS_ALLOW_ORIGINS="${PSOP_CORS_ALLOW_ORIGINS:-[\"*\"]}"
}

resolve_backend_python_bin() {
  local repo_root="${1}"

  if [[ -x "${repo_root}/backend/.venv/bin/python" ]]; then
    printf '%s\n' "${repo_root}/backend/.venv/bin/python"
    return 0
  fi

  if [[ -x "${repo_root}/backend/.venv/Scripts/python.exe" ]]; then
    printf '%s\n' "${repo_root}/backend/.venv/Scripts/python.exe"
    return 0
  fi

  return 1
}

require_backend_python() {
  local repo_root="${1}"
  local python_bin

  if ! python_bin="$(resolve_backend_python_bin "${repo_root}")"; then
    echo "backend/.venv is missing. Initialize the backend virtual environment first." >&2
    exit 1
  fi

  printf '%s\n' "${python_bin}"
}
