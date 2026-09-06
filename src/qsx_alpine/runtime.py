"""Standalone local Alpine instances. No source project code executes on the host."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import posixpath
import shlex
import stat
import tarfile
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .backend import LibvirtBackend, domain_xml
from .config import SAFE_NAME, load_policy, resolve_resources
from .errors import QSXError
from .templates import verify_image
from .util import atomic_json, run, sha256

WORKSPACE = "/workspace/project"
EXCLUDED_NAMES = {".git", ".ssh", ".gnupg", ".aws", ".codex", ".env", "id_rsa", "id_ed25519"}


@dataclass
class Instance:
    directory: Path
    metadata: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.metadata["name"])

    @property
    def disk(self) -> Path:
        return self.directory / "disk.qcow2"

    @property
    def manifest(self) -> Path:
        return self.directory / "instance.json"


def _save(instance: Instance) -> None:
    atomic_json(instance.manifest, instance.metadata)


def load_instance(path: Path | str) -> Instance:
    path = Path(path).expanduser().resolve()
    directory = path if path.is_dir() else path.parent
    manifest = directory / "instance.json"
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError) as exc:
        raise QSXError(f"cannot read instance: {exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 1
        or not isinstance(data.get("name"), str)
        or not SAFE_NAME.fullmatch(data["name"])
        or data.get("workspace") != WORKSPACE
        or not isinstance(data.get("instance_id"), str)
    ):
        raise QSXError("invalid standalone instance metadata")
    for filename in ("instance.json", "disk.qcow2", "domain.xml"):
        item = directory / filename
        if item.is_symlink() or not item.is_file():
            raise QSXError(f"missing or symbolic instance artifact: {filename}")
    return Instance(directory, data)


@contextmanager
def _locked(path: Path | str) -> Iterator[Instance]:
    instance = load_instance(path)
    fd = os.open(
        instance.directory / ".operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise QSXError("another instance lifecycle operation is active") from exc
        yield load_instance(instance.directory)
    finally:
        os.close(fd)


def _excluded(relative: Path) -> bool:
    return any(part in EXCLUDED_NAMES or part.startswith(".env.") for part in relative.parts)


def _source_entries(source: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for root, directories, files in os.walk(source, followlinks=False):
        parent = Path(root)
        directories[:] = sorted(
            name for name in directories if not _excluded((parent / name).relative_to(source))
        )
        for name in sorted([*directories, *files]):
            path = parent / name
            relative = path.relative_to(source)
            if _excluded(relative):
                continue
            info = path.lstat()
            entry: dict[str, Any] = {"path": relative.as_posix(), "mode": info.st_mode & 0o777}
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                resolved = posixpath.normpath(posixpath.join(relative.parent.as_posix(), target))
                if target.startswith("/") or resolved == ".." or resolved.startswith("../"):
                    raise QSXError(f"source symlink escapes or is absolute: {relative}")
                entry.update(type="symlink", target=target)
            elif stat.S_ISDIR(info.st_mode):
                entry.update(type="directory")
            elif stat.S_ISREG(info.st_mode):
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise QSXError(f"source changed object type: {relative}")
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                entry.update(type="file", size=info.st_size, sha256=digest)
            else:
                raise QSXError(f"unsupported source filesystem object: {relative}")
            entries.append(entry)
    return sorted(entries, key=lambda item: item["path"])


def _source_archive(source: Path | None, archive: Path) -> dict[str, Any]:
    before = _source_entries(source) if source else []
    with tarfile.open(archive, "w") as output:
        for entry in before:
            member = tarfile.TarInfo("project/" + entry["path"])
            member.mode = entry["mode"]
            member.uid = member.gid = 0
            if entry["type"] == "directory":
                member.type = tarfile.DIRTYPE
                output.addfile(member)
            elif entry["type"] == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = entry["target"]
                output.addfile(member)
            else:
                assert source is not None
                descriptor = os.open(source / entry["path"], os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as stream:
                    current = os.fstat(stream.fileno())
                    if not stat.S_ISREG(current.st_mode) or current.st_size != entry["size"]:
                        raise QSXError("source changed during snapshot")
                    member.size = current.st_size
                    output.addfile(member, stream)
    if source and _source_entries(source) != before:
        raise QSXError("source changed during snapshot; retry while source writers are stopped")
    return {
        "scope": "working-tree data only; fresh guest Git baseline; source Git history excluded",
        "entries": before,
        "manifest_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        "excluded_names": sorted(EXCLUDED_NAMES),
        "excluded_patterns": [".env.*"],
        "special_permission_bits": "stripped in imported copy",
        "archive_sha256": sha256(archive),
    }


def _seed(source: Path | None, destination: Path, instance_id: str, name: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=".seed-", dir=destination.parent) as temporary:
        root = Path(temporary)
        source_manifest = _source_archive(source, root / "project.tar")
        script = (
            "#!/bin/sh\nset -eu\numask 077\n"
            'test "$(id -u)" = 0\n'
            "test ! -e /var/lib/qsx-instance-id\n"
            "test ! -e /workspace/project/.git\n"
            "install -d -m 0755 -o agent -g agent /workspace/project\n"
            "tar --no-same-owner --same-permissions --delay-directory-restore "
            "-xf /mnt/qsx-import/project.tar -C /workspace/project --strip-components=1\n"
            "chown -R --no-dereference agent:agent /workspace/project\n"
            "su -s /bin/sh agent -c 'set -eu; cd /workspace/project; "
            "git init --initial-branch=task; "
            "git config core.hooksPath /dev/null; git config core.fsmonitor false; "
            'git config user.name "QSX Import"; git config user.email qsx-import@local.invalid; '
            'git add -A; git commit --allow-empty -m "QSX data import baseline"\'\n'
            "df -Pk /workspace/project > /var/lib/qsx-instance-filesystem.txt\n"
            f"printf '%s\\n' {shlex.quote(instance_id)} > /var/lib/qsx-instance-id\n"
            "chmod 0644 /var/lib/qsx-instance-id /var/lib/qsx-instance-filesystem.txt\n"
            "sync\n"
        )
        (root / "import.sh").write_text(script)
        (root / "meta-data").write_text(
            f"instance-id: {instance_id}\nlocal-hostname: {name.lower()}\n"
        )
        (root / "user-data").write_text(
            "#cloud-config\nssh_deletekeys: true\nssh_genkeytypes: []\n"
            "growpart: {mode: auto, devices: ['/']}\nresize_rootfs: true\n"
            "runcmd:\n  - [mkdir, -p, /mnt/qsx-import]\n"
            "  - [mount, -o, ro, 'LABEL=cidata', /mnt/qsx-import]\n"
            "  - [sh, /mnt/qsx-import/import.sh]\n"
            "  - [umount, /mnt/qsx-import]\n"
        )
        run(
            [
                "xorriso",
                "-as",
                "mkisofs",
                "-quiet",
                "-R",
                "-V",
                "cidata",
                "-o",
                str(destination),
                str(root),
            ]
        )
        destination.chmod(0o600)
        return source_manifest


def create_instance(
    name: str,
    image_manifest: Path | str,
    state_root: Path | str,
    source: Path | None = None,
    resources: dict[str, int] | None = None,
    policy: Path | None = None,
) -> dict[str, Any]:
    if not SAFE_NAME.fullmatch(name):
        raise QSXError("invalid instance name")
    started = time.monotonic()
    root = Path(state_root).expanduser().resolve()
    if root == Path("/"):
        raise QSXError("instance root cannot be filesystem root")
    image_manifest = Path(image_manifest).expanduser().resolve()
    manifest = verify_image(image_manifest)
    admitted_policy = load_policy(policy)
    admitted = resolve_resources(manifest.get("resources", {}), resources, admitted_policy)
    agent_argv = manifest.get("agent_argv")
    if (
        not isinstance(agent_argv, list)
        or not agent_argv
        or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in agent_argv)
    ):
        raise QSXError("image manifest requires an explicit verified agent_argv")
    if not isinstance(manifest.get("agent_profile_sha256"), str):
        raise QSXError("image manifest requires agent profile identity")
    if source is not None:
        source = source.expanduser().resolve()
        if not source.is_dir() or source == Path("/"):
            raise QSXError("source must be a safe directory")
        if root == source or root.is_relative_to(source) or source.is_relative_to(root):
            raise QSXError("source and instance storage boundaries must not overlap")
    base = image_manifest.parent / manifest["image"]
    base_info = json.loads(run(["qemu-img", "info", "--output=json", str(base)]))
    if base_info.get("virtual-size", 0) > admitted["disk_gib"] * 1024**3:
        raise QSXError("instance disk would shrink the verified base image")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = root / name
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise QSXError("instance directory already exists; refusing overwrite") from exc
    instance_id = "qsx-" + str(uuid.uuid4())
    try:
        disk = directory / "disk.qcow2"
        run(["qemu-img", "convert", "-f", "qcow2", "-O", "qcow2", str(base), str(disk)])
        run(["qemu-img", "resize", "-f", "qcow2", str(disk), f"{admitted['disk_gib']}G"])
        run(["qemu-img", "check", "-f", "qcow2", str(disk)])
        disk.chmod(0o600)
        imported = _seed(source, directory / "import.iso", instance_id, name)
        atomic_json(directory / "source-manifest.json", imported)
        atomic_json(directory / "image-manifest.json", manifest)
        xml = domain_xml(
            name, disk, directory / "import.iso", admitted["memory_mib"], admitted["vcpus"]
        )
        (directory / "domain.xml").write_text(xml)
        (directory / "domain.xml").chmod(0o600)
        data: dict[str, Any] = {
            "schema_version": 1,
            "name": name,
            "instance_id": instance_id,
            "instance_manifest": str(directory / "instance.json"),
            "workspace": WORKSPACE,
            "resources": admitted,
            "policy": admitted_policy,
            "policy_source": str(policy.resolve()) if policy else None,
            "image_sha256": manifest["image_sha256"],
            "recipe_sha256": manifest.get("recipe_sha256"),
            "agent_profile_sha256": manifest["agent_profile_sha256"],
            "agent_argv": agent_argv,
            "network_profile": "offline",
            "network_cut_latched": True,
            "domain_defined": False,
            "import_finalized": False,
            "phase": "prepared",
            "source_manifest_sha256": imported["manifest_sha256"],
            "created_at_unix": time.time(),
            "measurements": {
                "create_seconds": time.monotonic() - started,
                "virtual_size_bytes": admitted["disk_gib"] * 1024**3,
                "file_size_bytes": disk.stat().st_size,
                "allocated_bytes": disk.stat().st_blocks * 512,
                "guest_boot": "NOT_RUN",
                "copy_strategy": "standalone-qcow2-convert",
            },
        }
        atomic_json(directory / "instance.json", data)
        return data
    except BaseException:
        # Keep partial disk/import evidence. Never erase a possibly useful image
        # after an interrupted create; the directory is explicitly incomplete.
        atomic_json(
            directory / "creation-failed.json",
            {"instance_id": instance_id, "status": "FAIL_OR_INTERRUPTED"},
        )
        raise


def _runtime_policy(instance: Instance) -> None:
    if instance.metadata.get("network_profile") != "offline":
        raise QSXError("only an enforced offline instance can start")
    path = instance.metadata.get("policy_source")
    policy = load_policy(Path(path) if path else None)
    resolve_resources(instance.metadata["resources"], None, policy)


def start_instance(path: Path | str) -> dict[str, Any]:
    with _locked(path) as instance:
        _runtime_policy(instance)
        backend = LibvirtBackend()
        if not instance.metadata["domain_defined"]:
            backend.define(instance.directory / "domain.xml")
            instance.metadata["domain_defined"] = True
            _save(instance)
        requested = time.monotonic()
        backend.start(instance.name)
        instance.metadata["phase"] = "running"
        instance.metadata["last_start_unix"] = time.time()
        instance.metadata["measurements"]["launch_seconds"] = time.monotonic() - requested
        _save(instance)
        return {
            "name": instance.name,
            "state": "running",
            "import_finalized": instance.metadata["import_finalized"],
        }


def status_instance(path: Path | str) -> dict[str, Any]:
    instance = load_instance(path)
    state = (
        LibvirtBackend().status(instance.name)
        if instance.metadata["domain_defined"]
        else "not defined"
    )
    return {
        "name": instance.name,
        "state": state,
        "import_finalized": instance.metadata["import_finalized"],
        "network_profile": instance.metadata["network_profile"],
        "instance_manifest": str(instance.manifest),
    }


def stop_instance(path: Path | str, timeout: int = 120) -> dict[str, Any]:
    with _locked(path) as instance:
        if not instance.metadata["domain_defined"]:
            raise QSXError("instance domain has not been defined")
        backend = LibvirtBackend()
        if backend.is_active(instance.name):
            backend.shutdown(instance.name, timeout)
        instance.metadata["phase"] = "stopped"
        _save(instance)
        return {"name": instance.name, "state": "shut off"}


def finalize_import(path: Path | str) -> dict[str, Any]:
    with _locked(path) as instance:
        backend = LibvirtBackend()
        if instance.metadata["import_finalized"]:
            return {
                "name": instance.name,
                "import_finalized": True,
                "state": backend.status(instance.name),
            }
        observation_path = instance.directory / "import-observation.json"
        if instance.metadata.get("import_observation_sha256"):
            if sha256(observation_path) != instance.metadata["import_observation_sha256"]:
                raise QSXError("saved import observation changed; refusing finalization")
            observation = json.loads(observation_path.read_text())
        else:
            script = (
                f'test "$(cat /var/lib/qsx-instance-id)" = {shlex.quote(instance.metadata["instance_id"])} && '
                'test "$(id -u)" -ne 0 && git rev-parse HEAD && id && '
                "cat /etc/alpine-release && df -Pk /workspace/project"
            )
            observation = backend.run_guest(
                instance.name, ["sh", "-c", script], WORKSPACE, timeout=30
            )
            if observation["returncode"] != 0:
                raise QSXError("guest import is not ready; inspect boot console and retry")
            # Persist successful observation before shutdown so an interrupted
            # detach can resume without trying to execute in a stopped guest.
            atomic_json(observation_path, observation)
            instance.metadata["import_observation_sha256"] = sha256(observation_path)
            _save(instance)
        if backend.is_active(instance.name):
            backend.shutdown(instance.name)
        backend.detach_import_medium(instance.name, instance.directory / "import.iso")
        # Keep controller XML consistent for review and subsequent recovery.
        xml = domain_xml(
            instance.name,
            instance.disk,
            memory_mib=instance.metadata["resources"]["memory_mib"],
            vcpus=instance.metadata["resources"]["vcpus"],
        )
        (instance.directory / "domain.xml").write_text(xml)
        instance.metadata.update(import_finalized=True, phase="stopped")
        instance.metadata["measurements"]["guest_boot"] = "GUEST_REPORTED"
        instance.metadata["measurements"]["first_ready_elapsed_seconds"] = (
            time.time() - instance.metadata.get("last_start_unix", time.time())
        )
        _save(instance)
        return {
            "name": instance.name,
            "state": "shut off",
            "import_finalized": True,
            "observation": observation,
        }


def console_instance(path: Path | str) -> None:
    instance = load_instance(path)
    LibvirtBackend().console(instance.name)


def run_guest(path: Path | str, argv: list[str], timeout: int = 120) -> dict[str, object]:
    instance = load_instance(path)
    if not instance.metadata["import_finalized"]:
        raise QSXError("finalize the first-boot import before executing guest tasks")
    return LibvirtBackend().run_guest(instance.name, argv, WORKSPACE, timeout)


def run_agent(path: Path | str, extra_args: list[str] | None = None) -> None:
    instance = load_instance(path)
    if not instance.metadata["import_finalized"]:
        raise QSXError("finalize the first-boot import before launching the agent")
    argv = cast(list[str], instance.metadata["agent_argv"]) + (extra_args or [])
    LibvirtBackend().console(instance.name, shlex.join(argv), WORKSPACE)


def network_cut(path: Path | str) -> dict[str, object]:
    with _locked(path) as instance:
        # Persist denial before touching libvirt. Any failure must not restore
        # an earlier network grant on a later start or recovery attempt.
        instance.metadata["network_profile"] = "offline"
        instance.metadata["network_cut_latched"] = True
        _save(instance)
        if not instance.metadata["domain_defined"]:
            return {"network_profile": "offline", "active": False, "domain_defined": False}
        result = LibvirtBackend().network_cut(instance.name)
        atomic_json(instance.directory / "network-cut.json", result)
        return result
