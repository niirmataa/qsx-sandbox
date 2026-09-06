# QSX MVP

QSX is a conservative Python 3.12 controller for one `MathResearch` Ubuntu 24.04 libvirt/QEMU/KVM VM. Cloud validation covers Git, offline images, provisioning media, schemas, wheel installation, and adapter contracts—not KVM boot or network isolation.

## Reproducible developer setup

`requirements-dev.lock` pins the complete CPython 3.12/Linux x86_64 developer dependency set with wheel hashes. `codex/setup.sh` installs it with `--require-hashes`. Runtime is standard-library only and the repository includes a dependency-free PEP 517 wheel backend.
The lock was reproduced in a fresh venv with downloaded setup-phase wheels, `--no-index --require-hashes`, and `pip check`; wheel acceptance itself also runs without network access.

```bash
QSX_BUILD_CONTEXT=codex-cloud codex/setup.sh
make test-cloud lint typecheck security-audit install-wheel
```

## Operator flow

Copy `config/qsx.example.toml`, replace the base-image checksum with the checksum from the official Ubuntu release manifest, and point `base_image.path` at the locally staged image. QSX never downloads an image at runtime.

```bash
qsx doctor > doctor.json
qsx --config qsx.toml create task-001    # import, independent disk, ISO, define
qsx --config qsx.toml start              # KVM only; first boot imports then powers off
qsx --config qsx.toml status
qsx --config qsx.toml finalize-import task-001
qsx --config qsx.toml start              # agent works only in guest workspace
qsx --config qsx.toml shutdown
qsx --config qsx.toml checkpoint task-001
qsx --config qsx.toml rollback CHECKPOINT_ID
qsx --config qsx.toml export task-001 /new/review-destination
```

Export and checkpoint inspect the stopped guest via read-only libguestfs. Export creates a fresh directory containing Git history, baseline diff, and task metadata. There is no Docker dependency, writable source mount, credential provisioning, push, merge, or arbitrary host-shell API.

## Research Memory

Research Memory persists outside the VM and survives rollback. `memory-record` records research/evidence with explicit provenance, `handoff` records continuity, and `memory-render` regenerates CHANGELOG, MEMORY, LIFE, and CONTEXT. See `docs/RESEARCH_MEMORY.md`. Checkpoint lifecycle events are linked automatically; failed and NOT_RUN results remain visible.

The transactional SQLite ledger is authoritative; task-scoped JSONL and generated views are portable outputs. Use `history-collect/list/reconcile` for stopped-guest intake and recovery, and `context-build/context-check` for revision-bound handoff bundles. Local hash-chain consistency is not authenticity, completeness, trusted time, or proof of a scientific claim.

Portable bundles include full linked event records and a versioned exact-file manifest. Bundle checking reports integrity, task freshness, authenticity, and readiness separately. Research views use immutable generations selected by an atomic pointer, and idempotent operation IDs reject changed request content rather than silently discarding it. The RM01–RM08 test/evidence matrix is in `reports/RM_CLOSURE.md`.
