#!/usr/bin/env bash
# Check a cached environment; no network, package updates or host changes.
set -euo pipefail
[[ "${QSX_BUILD_CONTEXT:-}" == "codex-cloud" ]] || {
  printf '%s\n' 'Refusing outside the declared cloud build context.' >&2
  exit 64
}
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd -- "$ROOT"
[[ -x .venv/bin/python && -f .qsx-reports/setup-lock.sha256 ]] || {
  printf '%s\n' 'Environment is missing: rerun reviewed setup or reset its cache.' >&2
  exit 1
}
expected='BOOTSTRAP_NO_LOCK'
if [[ -f requirements-dev.lock ]]; then
  expected="$(sha256sum requirements-dev.lock | awk '{print $1}')"
fi
actual="$(cat .qsx-reports/setup-lock.sha256)"
[[ "$actual" == "$expected" ]] || {
  printf '%s\n' 'Dependency lock changed. Rerun setup/reset cache before the task.' >&2
  exit 1
}
.venv/bin/python -m pip check
python3 codex/capabilities.py > .qsx-reports/cloud-capabilities.json
printf '%s\n' 'Cached development environment checked; deployment remains untested.'
