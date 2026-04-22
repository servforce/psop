#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

bash "${SCRIPT_DIR}/require-node.sh"
bash "${SCRIPT_DIR}/require-web-deps.sh"

cd "${REPO_ROOT}/static"
npm test -- --runInBand
