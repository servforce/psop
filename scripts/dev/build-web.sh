#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/dev/common.sh
source "${SCRIPT_DIR}/common.sh"
REPO_ROOT="$(resolve_repo_root)"

load_psop_env "${REPO_ROOT}"
derive_local_integration_defaults

bash "${SCRIPT_DIR}/require-node.sh"
bash "${SCRIPT_DIR}/require-web-deps.sh"

cd "${REPO_ROOT}/static"
npm run build:css
