# Ubuntu 24.04 fortress-host acceptance (operator-run)

Run only after review and approval. Save every command, exit code, package version, and result as PASS/FAIL/BLOCKED/NOT RUN.

1. `qsx doctor | tee host-doctor.json`; verify `/dev/kvm`, `virsh --connect qemu:///system version`, QEMU, libvirt confinement, RAM, filesystem, free-space and permissions.
2. Define the generated XML, then `virsh start MathResearch` and `virsh domstate MathResearch`; prove KVM acceleration rather than mere device presence.
3. `qemu-img info /var/lib/qsx/MathResearch/disks/private.qcow2`; in the guest, verify partition/filesystem capacity is 60 GiB and the disk is private.
4. `virsh dumpxml MathResearch`; verify no `filesystem`, source-project path, Docker/libvirt socket, host home, USB storage, QMP exposure, or writable removable import medium.
5. With synthetic data, run create/import and hash source before/after. From guest attempt read/write of source and host sockets; all must fail.
6. Apply reviewed host firewall/profile rules. For `offline`, test DNS, IPv4, IPv6, UDP, LAN, host-admin and internet—all must fail. Do not infer isolation from absent NIC alone.
7. For `openai`, authenticate interactively as the guest user, prove the approved OpenAI route succeeds, and prove ordinary internet, direct DNS, IPv4/IPv6/UDP, LAN, metadata and host-admin fail. Stop the proxy and prove fail-closed behavior.
8. Shut down cleanly, checkpoint clean and dirty states, mutate disk/project, and rollback. Verify files, Git state, guest environment, and append-only controller lifecycle record. Verify active-image checkpoint refusal.
9. Export to a new destination; inspect branch, commits, baseline diff and metadata. Confirm no keys, auth caches or unrelated VM files. Re-hash the original source.
10. Copy a checkpoint to a separate encrypted medium, delete only a disposable working copy, restore and boot offline; compare recorded hashes.

Only after all checks pass may a report say `HOST_VALIDATED`. Production firewall installation is outside this cloud change.
