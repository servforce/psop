#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${PSOP_NODE_BIN_DIR:-}" ]]; then
  export PATH="${PSOP_NODE_BIN_DIR}:${PATH}"
elif [[ -x /opt/node20/bin/node && -x /opt/node20/bin/npm ]]; then
  export PATH="/opt/node20/bin:${PATH}"
fi

node_path="$(command -v node || true)"
npm_path="$(command -v npm || true)"

if [[ -z "${node_path}" || -z "${npm_path}" ]]; then
  echo "Missing Node.js runtime in this Linux environment." >&2
  if [[ -n "${npm_path}" && "${npm_path}" == /mnt/* ]]; then
    echo "Found Windows npm launcher at: ${npm_path}" >&2
  fi
  echo "Install a Linux-native node and npm inside WSL, then retry." >&2
  exit 1
fi

if [[ "${node_path}" == /mnt/* || "${node_path}" == *.exe ]]; then
  echo "Unsupported Node.js runtime for repo scripts: ${node_path}" >&2
  echo "Use a Linux-native node installation inside WSL instead of a Windows binary on /mnt/*." >&2
  exit 1
fi

if [[ "${npm_path}" == /mnt/* || "${npm_path}" == *.cmd || "${npm_path}" == *.exe ]]; then
  echo "Unsupported npm launcher for repo scripts: ${npm_path}" >&2
  echo "Use a Linux-native npm installation inside WSL instead of a Windows launcher on /mnt/*." >&2
  exit 1
fi
