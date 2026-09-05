# Cloud validation — 2026-09-05

Context: Codex Cloud runner, not target hardware. Python 3.12.13; Git 2.43.0; qemu-img/QEMU 8.2.2; virsh 10.0.0; x86_64 Ubuntu 24.04. Evidence commands: `python3 codex/capabilities.py`, `make test-cloud`, `make lint`, `make typecheck`, `make security-audit`, `qsx --help`, and `qsx doctor`.

| IDs/scope | Status | Evidence layer | Limitation |
|---|---|---|---|
| T01–T04, T06, T10–T12 cloud | PASS | real temporary filesystems and real Git | no guest VM involved |
| T07–T08 cloud | PASS | real offline qemu-img QCOW2 create/check/copy/restore | not a booted guest recovery |
| T05 cloud XML contract | PASS | generated production domain XML | host confinement BLOCKED |
| T09 cloud profile validation | PASS | configuration/schema | actual network isolation BLOCKED |
| T13 cloud atomic import/export | PASS | unit/integration | crash injection NOT RUN |
| T14 cloud repository scan | PASS | security-audit | heuristic, snapshots may contain secrets |
| T15 cloud | NOT RUN | none | needs separate storage |
| Real KVM, VM boot, firewall, OpenAI route | BLOCKED | capability inventory: `/dev/kvm` absent | requires physical host |
| Full TCG boot | NOT RUN | optional | not required and no approved guest image |

All mandatory cloud implementation tests pass, so status is **CLOUD_VALIDATED**. Status is explicitly not HOST_VALIDATED.
