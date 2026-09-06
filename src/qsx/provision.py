from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from .config import Config
from .errors import QSXError
from .util import atomic_json, run, sha256


def workspace_fingerprint(root: Path) -> str:
    """Hash a canonical v1 manifest; `.git` is the sole excluded top-level tree."""
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        info = path.lstat()
        entry: dict[str, object] = {
            "path_utf8": str(relative),
            "mode": info.st_mode & 0o777,
        }
        if path.is_symlink():
            entry.update({"type": "symlink", "target_utf8": os.readlink(path)})
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            entry.update({"type": "file", "size": info.st_size, "sha256": digest.hexdigest()})
        elif path.is_dir():
            entry.update({"type": "directory"})
        else:
            raise QSXError(f"unsupported workspace object: {relative}")
        entries.append(entry)
    manifest = {"schema": "qsx-workspace-manifest-v1", "excluded": [".git/**"], "entries": entries}
    encoded = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class GuestProvisioner:
    """Build a private disk and a one-shot, read-only import medium while the VM is off."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def provision(self, task_id: str) -> dict[str, str]:
        if not self.config.base_image or not self.config.base_image_sha256:
            raise QSXError("provisioning requires a pinned local base image and SHA-256")
        if not self.config.base_image.is_file():
            raise QSXError("pinned base image is unavailable; runtime downloads are forbidden")
        if sha256(self.config.base_image) != self.config.base_image_sha256:
            raise QSXError("base image SHA-256 mismatch")
        image_info = json.loads(
            run(["qemu-img", "info", "--output=json", str(self.config.base_image)])
        )
        if image_info.get("format") != "qcow2":
            raise QSXError("the pinned base image must be QCOW2")
        task_root = self.config.state_dir / "tasks" / task_id
        workspace = task_root / "workspace"
        if not workspace.is_dir():
            raise QSXError("private import staging is incomplete")
        if self.config.disk.exists():
            raise QSXError("refusing to overwrite an existing private disk")
        self.config.disk.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_disk = self.config.disk.with_suffix(".provisioning")
        medium = task_root / "import.iso"
        try:
            run(
                [
                    "qemu-img",
                    "convert",
                    "-f",
                    "qcow2",
                    "-O",
                    "qcow2",
                    str(self.config.base_image),
                    str(temporary_disk),
                ]
            )
            run(
                [
                    "qemu-img",
                    "resize",
                    "-f",
                    "qcow2",
                    str(temporary_disk),
                    f"{self.config.disk_gib}G",
                ]
            )
            self._make_medium(workspace, medium)
            temporary_disk.replace(self.config.disk)
        except BaseException:
            temporary_disk.unlink(missing_ok=True)
            medium.unlink(missing_ok=True)
            raise
        result = {
            "disk": str(self.config.disk),
            "import_medium": str(medium),
            "guest_workspace": self.config.guest_workspace,
            "base_image_sha256": self.config.base_image_sha256,
        }
        atomic_json(task_root / "provision.json", result)
        return result

    def _make_medium(self, workspace: Path, destination: Path) -> None:
        with tempfile.TemporaryDirectory(dir=self.config.state_dir) as temporary_name:
            temporary = Path(temporary_name)
            archive = temporary / "workspace.tar"
            with tarfile.open(archive, "w") as output:
                output.add(workspace, arcname="project", recursive=True)
            bootstrap = temporary / "qsx-import.sh"
            bootstrap.write_text(
                "#!/bin/sh\nset -eu\n"
                'test "$(id -u)" = 0\n'
                "install -d -o agent -g agent -- '" + self.config.guest_workspace + "'\n"
                "tar --no-same-owner --no-same-permissions -xf /mnt/qsx-import/workspace.tar "
                "-C '" + self.config.guest_workspace + "' --strip-components=1\n"
                "chown -R agent:agent -- '" + self.config.guest_workspace + "'\n"
                "touch /var/lib/qsx-import-complete\n",
                encoding="utf-8",
            )
            bootstrap.chmod(0o500)
            (temporary / "meta-data").write_text("instance-id: qsx-import\n")
            (temporary / "user-data").write_text(
                "#cloud-config\nusers:\n  - default\n  - name: agent\n"
                "    lock_passwd: true\n    shell: /bin/bash\nruncmd:\n"
                "  - [mkdir, -p, /mnt/qsx-import]\n"
                "  - [mount, -o, ro, 'LABEL=cidata', /mnt/qsx-import]\n"
                "  - [sh, /mnt/qsx-import/qsx-import.sh]\n"
                "  - [umount, /mnt/qsx-import]\n"
                "power_state: {mode: poweroff, timeout: 120}\n"
            )
            run(
                [
                    "xorriso",
                    "-as",
                    "mkisofs",
                    "-quiet",
                    "-V",
                    "cidata",
                    "-o",
                    str(destination),
                    str(temporary),
                ]
            )


class OfflineGuestExport:
    """Extract a stopped guest workspace through libguestfs, never a kernel mount."""

    def extract(self, disk: Path, guest_workspace: str, destination: Path) -> Path:
        if not shutil.which("virt-copy-out"):
            raise QSXError("virt-copy-out is required for controlled offline guest export")
        destination.mkdir(parents=True, mode=0o700)
        run(
            [
                "virt-copy-out",
                "-a",
                str(disk),
                "--format=qcow2",
                "--ro",
                guest_workspace,
                str(destination),
            ]
        )
        extracted = destination / Path(guest_workspace).name
        if not extracted.is_dir():
            raise QSXError("guest export did not produce the expected workspace")
        return extracted


def inspect_offline_workspace(
    config: Config, task_id: str
) -> tuple[str, str, bool, str | None, str]:
    """Observe Git inside a stopped disk; the host import staging is never authoritative."""
    del task_id
    with tempfile.TemporaryDirectory(dir=config.state_dir) as temporary:
        workspace = OfflineGuestExport().extract(
            config.disk, config.guest_workspace, Path(temporary)
        )
        branch = run(["git", "branch", "--show-current"], workspace)
        commit = run(["git", "rev-parse", "HEAD"], workspace)
        status = run(["git", "status", "--porcelain=v1"], workspace)
        dirty_hash = hashlib.sha256(status.encode()).hexdigest() if status else None
        return branch, commit, bool(status), dirty_hash, workspace_fingerprint(workspace)
