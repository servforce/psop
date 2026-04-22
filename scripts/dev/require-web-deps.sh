#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STATIC_DIR="${REPO_ROOT}/static"

if [[ ! -d "${STATIC_DIR}/node_modules" ]]; then
  echo "Frontend dependencies are not installed." >&2
  echo "Run 'cd ${STATIC_DIR} && npm ci' first, then retry." >&2
  exit 1
fi

if [[ ! -f "${STATIC_DIR}/node_modules/@tailwindcss/postcss/package.json" ]]; then
  echo "Frontend dev dependency '@tailwindcss/postcss' is missing." >&2
  echo "Reinstall frontend dependencies with 'cd ${STATIC_DIR} && npm ci'." >&2
  exit 1
fi
