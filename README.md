# QSX MVP

QSX is a conservative Python 3.12 controller for one `MathResearch` workspace and a future Ubuntu 24.04 libvirt/QEMU/KVM VM. The cloud build validates project-state logic and **offline** QCOW2 operations; it does not claim host or network isolation.

## Setup and use

```bash
QSX_BUILD_CONTEXT=codex-cloud codex/setup.sh
.venv/bin/pip install --no-deps -e .
qsx --help
qsx doctor > doctor.json
cp config/qsx.example.toml qsx.toml  # edit source/state/disk
qsx --config qsx.toml create task-001
qsx --config qsx.toml resume task-001
qsx --config qsx.toml checkpoint task-001  # domain must be SHUTOFF
qsx --config qsx.toml export task-001 /new/review-destination
```

`create` copies into private state, creates a technical import baseline, and switches to `agent/mathresearch/<task-id>`. Existing tasks retain their run ID and branch. Source history is transported through a bundle, never a shared clone. The source is checked before/after copying. External symlinks and incomplete Git modes are refused. Normal export creates a fresh directory containing Git history, baseline diff, and task metadata; it never writes the input.

The CLI has no Docker, credentials, push, merge, writable source mount, or arbitrary-shell facility. `offline` and `openai` are configuration intentions until the host acceptance proves external enforcement. Run `make test-cloud`, `make lint`, and `make typecheck`.
