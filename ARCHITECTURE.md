# Architecture

The CLI loads validated TOML and calls independent modules: `workspace` owns immutable-source import, task identity, Git baseline/resume, and export; `disk` owns cold QCOW2 copies and rescue-first rollback; `backend` is the production `virsh qemu:///system` boundary; and `doctor` performs read-only discovery. Commands are argument arrays, never shells.

The controller's task JSON is authoritative and outside the agent workspace. A checkpoint is a disk copy plus an atomic manifest, not a Git commit. It records commit and dirty state but protects the entire disk. Production start requires KVM and has no TCG fallback. Domain XML attaches one private QCOW2 disk and no host filesystem or network device. Network profiles are policy labels awaiting a separately reviewed host enforcement implementation and test.

This MVP intentionally excludes MCP, browsers, clusters, remote desktop, live snapshots, and firewall mutation. Ubuntu 24.04 x86_64 with libvirt/QEMU/KVM is the first target; exact host compatibility is determined by local acceptance.
