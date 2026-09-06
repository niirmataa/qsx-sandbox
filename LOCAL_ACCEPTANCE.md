# Ubuntu 24.04 fortress-host acceptance (operator-run)

Run only after review and approval. Record every command, exit code, version, and PASS/FAIL/BLOCKED/NOT RUN result.

1. Run `qsx doctor | tee host-doctor.json`; verify `/dev/kvm`, actual acceleration, `virsh --connect qemu:///system version`, QEMU confinement, RAM, filesystem, free space, and controller permissions.
2. Download the documented Ubuntu 24.04 image during controlled setup only. Verify its checksum against the official HTTPS `SHA256SUMS`, record exact release/build, configure the local path/hash, disconnect setup access, and run `qsx create task-001`.
3. Inspect `qemu-img info` and `virsh dumpxml MathResearch`: independent 60 GiB private QCOW2, read-only import ISO, no backing file, network interface, host `filesystem`, source path, Docker/libvirt socket, host home, USB storage, or QMP exposure.
4. Hash synthetic source before/after. Start the first boot; verify cloud-init copies Git to `/home/agent/workspace/<project>` on the private filesystem and powers off. Run `qsx finalize-import task-001`; prove the ISO is detached before agent work and guest access/write attempts against source and host sockets fail.
5. Start again, commit and leave separate uncommitted changes inside the guest, then cleanly `qsx shutdown`. Verify `qsx checkpoint task-001` observes the guest-disk branch/commit/dirty state using read-only libguestfs, not controller staging.
6. Mutate OS and committed/uncommitted project state, shut down, rollback, boot offline, and verify system, workspace, branch, commits, and dirty files are restored. Verify lifecycle logging remains append-only, active restore is refused, tampered checkpoint/hash is refused, and a unique rescue image precedes replacement.
7. Apply separately reviewed host network rules. For `offline`, test DNS, IPv4, IPv6, UDP, LAN, metadata, host-admin, and internet—all fail. For `openai`, prove only the approved authenticated OpenAI path works; ordinary internet and direct/alternate routes fail, including fail-closed proxy outage.
8. Shut down and export through libguestfs to a new directory. Inspect branch, commits, baseline diff, and metadata; confirm no keys/auth caches/unrelated VM files. Re-hash original source.
9. Copy a checkpoint to separate encrypted storage, remove only a disposable working copy, restore, boot offline, and compare hashes.

Only after all checks pass may a report say `HOST_VALIDATED`. Cloud results do not satisfy these physical tests.

## Research-memory acceptance

Create CP-A, CP-B, roll back to CP-A, then create CP-C. Verify CP-C names CP-A as parent while LIFE retains creation of A/B, the rollback, and creation of C. Record an agent FAIL and separate independently verified evidence; ensure both survive rollback and appear with distinct provenance in current CONTEXT and handoff. Confirm the journal and generated views reside under controller state, not inside the restored disk.

Collect a real stopped-guest outbox twice and verify one controller event per operation ID, `GUEST_REPORTED` provenance regardless of claimed role, and no guest access to `ledger.sqlite3`. Build a handoff bundle, consume its revision in a new session, change guest HEAD/content, and prove the old bundle becomes STALE. Copy ledger/JSONL, manifests, disk and approved artifacts to a genuinely separate protected medium; restore on a new trusted host and report missing artifacts/unsigned trust anchors explicitly. These remain BLOCKED until performed physically.

For RM01–RM08, move a generated bundle to independent review storage and verify every Markdown event link, the exact file inventory, task-filtered JSON/JSONL equality, artifact hashes, and separate integrity/freshness/authenticity/readiness results. Repeat after a different task receives an unrelated note (original task remains CURRENT), then after the original guest HEAD/workspace changes (STALE). Exercise a real rollback with a stable operation ID and confirm a changed target conflicts before disk mutation. These physical-guest checks remain BLOCKED until operator execution.
