#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/dev/common.sh
source "${SCRIPT_DIR}/common.sh"
REPO_ROOT="$(resolve_repo_root)"

load_psop_env "${REPO_ROOT}"
derive_local_integration_defaults
PYTHON_BIN="$(require_backend_python "${REPO_ROOT}")"

cd "${REPO_ROOT}"
"${PYTHON_BIN}" -m pytest tests -q
