# Threat model

Project files, dependencies, guest, and agent are untrusted. The host kernel, libvirt/QEMU confinement, firmware, controller state, and operator are trusted. QSX avoids source mounts, host sockets, credentials, automatic network fallback, push, merge, and execution of imported code. It rejects escaping symlinks, special files, external gitdirs, submodules, LFS, shallow and sparse repositories.

Git rules and manifests provide auditability, not a security boundary. The current copy is consistency-checked but is not an atomic filesystem snapshot. Secret-name exclusion is defense in depth, not proof that content is secret-free. Checkpoints can contain guest credentials and require protected permissions/backups. QSX does not promise defense against a malicious host administrator, hypervisor escape, hardware attacks, resource exhaustion, or unvalidated network rules.
