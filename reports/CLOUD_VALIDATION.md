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
| Research Memory v1 | PASS | real append/fsync/hash-chain, generated views, negative results, handoff, Git/checkpoint links, rollback lineage | controller filesystem test; not a VM boot |
| T13 interruption injection | NOT RUN | normal atomic staging is tested | power-loss injection not executed |
| T14 repository scan | PASS | security audit | heuristic; snapshots may contain secrets |
| T15 secondary media | NOT RUN | none | requires operator storage |
| Real KVM/VM boot/firewall/OpenAI route | BLOCKED | `/dev/kvm` absent and no target host | physical-host acceptance required |
| Optional TCG boot | NOT RUN | no approved boot fixture | never a production fallback |

Mandatory cloud gates executed here are PASS. This is `CLOUD_VALIDATED` for implementation and offline integration only; it is explicitly not `HOST_VALIDATED`, and no claim is made that a booted rollback or network boundary works.
