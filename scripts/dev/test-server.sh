#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/backend/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "backend/.venv is missing. Initialize the backend virtual environment first." >&2
  exit 1
fi

cd "${REPO_ROOT}"
"${PYTHON_BIN}" -m pytest tests -q
