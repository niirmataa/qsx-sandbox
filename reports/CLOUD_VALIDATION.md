# Cloud validation — 2026-09-05

Context is a Codex Cloud runner, not target hardware. Python 3.12.13; Git 2.43.0; qemu-img/QEMU 8.2.2; virsh 10.0.0; xorriso available; `/dev/kvm` absent. Evidence commands are `python3 codex/capabilities.py`, `make test-cloud`, `make lint`, `make typecheck`, `make security-audit`, and `make install-wheel`.

| IDs/scope | Status | Evidence layer | Limitation |
|---|---|---|---|
| T01–T04, T06, T10–T12 cloud | PASS | real temporary filesystems and real Git | source never modified; no guest boot |
| Guest provisioning cloud | PASS | verified synthetic base, real qemu-img convert/resize, real xorriso read-only NoCloud medium | synthetic image is deliberately not bootable |
| T05 cloud XML contract | PASS | production XML has private disk/read-only ISO and no filesystem/network | actual guest/host confinement BLOCKED |
| T07–T08 offline image mechanics | PASS | real qemu-img check/copy/hash/restore and active refusal contract | full OS/workspace boot restore BLOCKED |
| Checkpoint metadata source | PASS | contract requires read-only extraction from stopped guest disk | libguestfs/real guest execution BLOCKED here |
| T09 schema | PASS | only `offline`/`openai` accepted | actual network enforcement BLOCKED |
| Hardened rollback | PASS | malformed/traversal IDs, incomplete/tampered image, hash and unique rescues tested | full guest recovery BLOCKED |
| Offline wheel acceptance | PASS | wheel built without downloads, fresh venv, install `--no-index`, help/doctor outside repo | none for installability |
| Dependency lock reproduction | PASS | fresh venv, locally cached setup wheels, `pip --require-hashes --no-index`, then `pip check` | lock targets CPython 3.12/Linux x86_64 |
| Research Memory RM01–RM08 | PASS | real SQLite/WAL, complete manifested exports, scoped revisions/idempotency, correction semantics, verification validation, byte budget, immutable generations and controlled concurrency/fault injection | synthetic controller filesystems; see `RM_CLOSURE.md`; physical guest collection BLOCKED |
| T13 interruption injection | NOT RUN | normal atomic staging is tested | power-loss injection not executed |
| T14 repository scan | PASS | security audit | heuristic; snapshots may contain secrets |
| T15 secondary media | NOT RUN | none | requires operator storage |
| Real KVM/VM boot/firewall/OpenAI route | BLOCKED | `/dev/kvm` absent and no target host | physical-host acceptance required |
| Optional TCG boot | NOT RUN | no approved boot fixture | never a production fallback |

The expanded suite executed 51 tests at code revision `499ec1a6ebd519bfb6fb2e17aa32a3d714af2b1b`. Mandatory cloud gates executed here are PASS. This is `CLOUD_VALIDATED` for implementation and offline integration only; it is explicitly not `HOST_VALIDATED`, and no claim is made that a booted rollback or network boundary works.

## Synthetic lineage evidence

The real-qemu-img controller test produced the logical sequence `CP-A → CP-B → rollback(CP-A) → CP-C`. Assertions verified that CP-C's parent is CP-A, CP-B remains in chronological LIFE, rollback establishes a new lineage, and controller memory is not restored with the disk. This is Cloud evidence for metadata/controller behavior, not proof that a bootable guest was restored.

## Remaining gaps

PARTIAL: fault injection does not yet cover every filesystem crash point after QCOW2 replacement; Git/checkpoint evidence has integration coverage but not every cross-task/corruption combination; context is built for a read-only delivery medium but attaching/consuming that medium is physical acceptance. MISSING/NOT RUN: signed external trust anchors and protected signing keys, real separate-media restoration, and trusted-time validation. BLOCKED: real stopped-guest outbox collection, boot/rollback, KVM, libvirt confinement, and network isolation on the operator host.
