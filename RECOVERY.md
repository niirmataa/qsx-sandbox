# Recovery

1. Stop the guest cleanly and verify `virsh domstate MathResearch` reports `shut off`.
2. Create a checkpoint; inspect its `manifest.json`, `qemu-img check`, free space, and protected permissions.
3. Copy the checkpoint directory to operator-selected encrypted secondary storage and verify SHA-256 there.
4. Roll back only after review. Default rollback copies the current disk into `state_dir/rescue` before atomic replacement. `--no-rescue` is an explicit operator opt-out.
5. Start restored state with the offline profile, inspect Git/files/rules/credentials, then deliberately re-enable allowed connectivity.

A full checkpoint is not an independent backup. Never manipulate an active image with `qemu-img` or filesystem copying.

Research Memory restoration requires an independently copied `events.jsonl`, task/project identity, state/checkpoint manifests, approved artifacts, configuration versions, and—when later implemented—an independently provisioned verification key and anchor. Restore into an empty ledger, verify local chain/project identity, and treat every missing artifact as missing. A same-disk copy is not an independent witness or backup. The current v1 unsigned anchor cannot establish authenticity against a compromised controller.
