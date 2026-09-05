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
