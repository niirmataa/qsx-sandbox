#!/usr/bin/env bash
# Development dependencies for an ephemeral Codex Cloud runner, not a host installer.
set -euo pipefail

if [[ "${QSX_BUILD_CONTEXT:-}" != "codex-cloud" ]]; then
  printf '%s\n' 'Refusing: set QSX_BUILD_CONTEXT=codex-cloud in the cloud environment.' >&2
  exit 64
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd -- "$ROOT"
if ! command -v apt-get >/dev/null 2>&1; then
  printf '%s\n' 'This bootstrap supports an apt-based Linux runner only.' >&2
  exit 69
fi
SUDO=()
if [[ "$(id -u)" != "0" ]]; then
  if ! command -v sudo >/dev/null 2>&1 || ! sudo -n true; then
    printf '%s\n' 'Package installation is unavailable; fix environment setup, do not bypass it.' >&2
    exit 77
  fi
  SUDO=(sudo -n)
fi

"${SUDO[@]}" apt-get update
"${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  ca-certificates git make python3-venv qemu-utils qemu-system-x86 \
  libvirt-clients libxml2-utils shellcheck xorriso rsync openssh-client

python3 -c 'import sys; assert sys.version_info >= (3, 12), "Select Python 3.12 or newer in the environment"'
python3 -m venv .venv
mkdir -p .qsx-reports
if [[ -f requirements-dev.lock ]]; then
  # This file must contain a complete pip-compatible lock with hashes.
  .venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
else
  printf '%s\n' 'BOOTSTRAP ONLY: no reviewed dependency lock exists yet.' >&2
  .venv/bin/python -m pip install pytest ruff mypy build
fi
.venv/bin/python -m pip check
.venv/bin/python -m pip freeze --all > .qsx-reports/bootstrap-python.txt
dpkg-query -W -f='${Package}\t${Version}\n' \
  git qemu-utils qemu-system-x86 libvirt-clients libxml2-utils shellcheck \
  > .qsx-reports/bootstrap-system.txt
python3 codex/capabilities.py > .qsx-reports/cloud-capabilities.json
if [[ -f requirements-dev.lock ]]; then
  sha256sum requirements-dev.lock | awk '{print $1}' > .qsx-reports/setup-lock.sha256
else
  printf '%s\n' 'BOOTSTRAP_NO_LOCK' > .qsx-reports/setup-lock.sha256
fi
printf '%s\n' 'Development tools installed. No host VM, networking or OpenAI login was configured.'
