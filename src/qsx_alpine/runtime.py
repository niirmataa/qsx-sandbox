"""Standalone local Alpine instances. No source project code executes on the host."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shlex
import stat
import tarfile
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast

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


def _source_state(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _source_open(name: Path | str, parent: int | None, flags: int) -> Iterator[int]:
    descriptor = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _source_root(source: Path) -> Iterator[int]:
    # Pin the selected directory when snapshotting starts. Even its ancestors
    # must not be re-resolved through links between validation and opening.
    with ExitStack() as stack:
        descriptor = stack.enter_context(_source_open("/", None, os.O_PATH | os.O_DIRECTORY))
        for component in source.absolute().parts[1:]:
            descriptor = stack.enter_context(
                _source_open(component, descriptor, os.O_PATH | os.O_DIRECTORY)
            )
        # Ancestors need search permission only; the selected directory is listed.
        yield stack.enter_context(_source_open(".", descriptor, os.O_RDONLY | os.O_DIRECTORY))


def _source_entries(source: int) -> tuple[list[dict[str, Any]], dict[str, tuple[int, ...]]]:
    entries: list[dict[str, Any]] = []
    states: dict[str, tuple[int, ...]] = {}

    def visit(directory: int, parent: Path) -> None:
        initial = _source_state(os.fstat(directory))
        states[parent.as_posix()] = initial
        for name in sorted(os.listdir(directory)):
            relative = parent / name
            if _excluded(relative):
                continue
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            expected = _source_state(info)
            entry: dict[str, Any] = {"path": relative.as_posix(), "mode": info.st_mode & 0o777}
            if stat.S_ISLNK(info.st_mode):
                entry.update(type="symlink", target=os.readlink(name, dir_fd=directory))
            elif stat.S_ISDIR(info.st_mode):
                with _source_open(name, directory, os.O_RDONLY | os.O_DIRECTORY) as child:
                    if _source_state(os.fstat(child)) != expected:
                        raise QSXError("source changed during snapshot")
                    visit(child, relative)
                entry.update(type="directory")
            elif stat.S_ISREG(info.st_mode):
                with _source_open(name, directory, os.O_RDONLY) as descriptor, os.fdopen(
                    descriptor, "rb", closefd=False
                ) as stream:
                    if _source_state(os.fstat(descriptor)) != expected:
                        raise QSXError("source changed during snapshot")
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    if _source_state(os.fstat(descriptor)) != expected:
                        raise QSXError("source changed during snapshot")
                entry.update(type="file", size=info.st_size, sha256=digest)
            else:
                raise QSXError(f"unsupported source filesystem object: {relative}")
            if _source_state(os.stat(name, dir_fd=directory, follow_symlinks=False)) != expected:
                raise QSXError("source changed during snapshot")
            states[relative.as_posix()] = expected
            entries.append(entry)
        if _source_state(os.fstat(directory)) != initial:
            raise QSXError("source changed during snapshot")

    visit(source, Path())
    return sorted(entries, key=lambda item: item["path"]), states


def _source_links(entries: list[dict[str, Any]]) -> None:
    links = {entry["path"]: entry["target"] for entry in entries if entry["type"] == "symlink"}
    for name, target in links.items():
        resolved = list(Path(name).parent.parts)
        pending = target.split("/")
        followed = 0
        while pending:
            component = pending.pop(0)
            if target.startswith("/") or (component == ".." and not resolved):
                raise QSXError(f"source symlink escapes or is absolute: {name}")
            if component == "..":
                resolved.pop()
            elif component not in ("", "."):
                resolved.append(component)
                link = "/".join(resolved)
                if link in links:
                    followed += 1
                    if followed > 40:
                        raise QSXError(f"source symlink cycle or excessive chain: {name}")
                    target = links[link]
                    resolved.pop()
                    pending = target.split("/") + pending


class _ArchiveReader:
    """tarfile consumes read(); hash those exact bytes, without buffering a file."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        data = self.stream.read(size)
        self.digest.update(data)
        self.size += len(data)
        return data


def _archive_source_file(
    source: int, entry: dict[str, Any], states: dict[str, tuple[int, ...]],
    output: tarfile.TarFile, member: tarfile.TarInfo,
) -> None:
    with ExitStack() as stack:
        directory = source
        relative = Path()
        for component in Path(entry["path"]).parts[:-1]:
            relative /= component
            directory = stack.enter_context(
                _source_open(component, directory, os.O_RDONLY | os.O_DIRECTORY)
            )
            if _source_state(os.fstat(directory)) != states[relative.as_posix()]:
                raise QSXError("source changed during snapshot")
        descriptor = stack.enter_context(
            _source_open(Path(entry["path"]).name, directory, os.O_RDONLY)
        )
        expected = states[entry["path"]]
        if _source_state(os.fstat(descriptor)) != expected:
            raise QSXError("source changed during snapshot")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            reader = _ArchiveReader(stream)
            member.size = entry["size"]
            output.addfile(member, cast(BinaryIO, reader))
            if (reader.size != entry["size"] or reader.digest.hexdigest() != entry["sha256"]
                    or _source_state(os.fstat(descriptor)) != expected):
                raise QSXError("source changed during snapshot")


def _source_archive(source: Path | None, archive: Path) -> dict[str, Any]:
    try:
        with ExitStack() as stack:
            descriptor = stack.enter_context(_source_root(source)) if source is not None else None
            before, states = _source_entries(descriptor) if descriptor is not None else ([], {})
            _source_links(before)
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
                        assert descriptor is not None
                        _archive_source_file(descriptor, entry, states, output, member)
            if descriptor is not None and _source_entries(descriptor) != (before, states):
                raise QSXError("source changed during snapshot; retry while writers are stopped")
    except OSError as exc:
        raise QSXError(f"source snapshot failed: {exc}") from exc
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


def _runtime_policy(instance: Instance) -> dict[str, Any] | None:
    from .runtime_egress import PROFILE, load_egress_policy, policy_digest

    grant = None
    if instance.metadata.get("network_profile") != "offline":
        if (instance.metadata.get("network_profile") != PROFILE
                or instance.metadata.get("network_cut_latched") is not False):
            raise QSXError("egress grant is absent or network-cut remains latched")
        grant = instance.metadata.get("egress_grant")
        if (not isinstance(grant, dict) or grant.get("revoked_at_unix") is not None
                or any(not isinstance(grant.get(key), str) for key in (
                    "policy_source", "policy_sha256", "mac", "boot_id", "deployment_sha256"
                ))):
            raise QSXError("egress requires a current explicit instance grant")
        current = load_egress_policy(Path(grant["policy_source"]), instance.metadata["instance_id"])
        if policy_digest(current) != grant.get("policy_sha256"):
            raise QSXError("egress operator policy changed since the explicit grant")
    path = instance.metadata.get("policy_source")
    policy = load_policy(Path(path) if path else None)
    resolve_resources(instance.metadata["resources"], None, policy)
    return grant


def start_instance(path: Path | str) -> dict[str, Any]:
    with _locked(path) as instance:
        grant = _runtime_policy(instance)
        pending = instance.metadata.get("egress_pending")
        if pending:
            medium = instance.directory / pending["medium"]
            if (medium.name != pending["medium"] or medium.is_symlink() or not medium.is_file()
                    or sha256(medium) != pending["medium_sha256"]):
                raise QSXError("private egress configuration medium changed before startup")
        backend = LibvirtBackend()
        if not instance.metadata["domain_defined"]:
            backend.define(instance.directory / "domain.xml")
            instance.metadata["domain_defined"] = True
            _save(instance)
        requested = time.monotonic()
        if grant is None:
            backend.start(instance.name)
        else:
            backend.start(instance.name, egress=grant)
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
        if instance.metadata.get("egress_grant"):
            instance.metadata["egress_grant"]["revoked_at_unix"] = time.time()
        if instance.metadata.get("egress_pending"):
            instance.metadata["egress_pending"]["revoked"] = True
        _save(instance)
        if not instance.metadata["domain_defined"]:
            return {"network_profile": "offline", "active": False, "domain_defined": False}
        backend = LibvirtBackend()
        result = backend.network_cut(instance.name)
        (instance.directory / "domain.xml").write_text(backend.definition(instance.name))
        atomic_json(instance.directory / "network-cut.json", result)
        return result


def prepare_network(path: Path | str, policy: Path) -> dict[str, Any]:
    """Import public network settings into one private VM, still completely offline."""
    from .runtime_egress import check_domain, load_egress_policy, make_seed, policy_digest

    with _locked(path) as instance:
        admitted = load_egress_policy(policy, instance.metadata["instance_id"])
        if not instance.metadata["import_finalized"] or not instance.metadata["domain_defined"]:
            raise QSXError("finalize the project import before preparing private egress")
        if instance.metadata["network_profile"] != "offline":
            raise QSXError("network-cut is required before preparing another explicit grant")
        backend = LibvirtBackend()
        if backend.is_active(instance.name):
            raise QSXError("stop the instance before preparing its egress configuration medium")
        check_domain(backend._domain(instance.name, inactive=True))
        pending = instance.metadata.get("egress_pending")
        if pending and pending.get("revoked"):
            backend.detach_medium(instance.name, instance.directory / pending["medium"], missing_ok=True)
            instance.metadata.setdefault("egress_preparation_history", []).append(pending)
            pending = None
        digest = policy_digest(admitted)
        if pending:
            if pending.get("policy_sha256") != digest:
                raise QSXError("pending egress policy differs; network-cut before new preparation")
            medium = instance.directory / pending["medium"]
            if sha256(medium) != pending["medium_sha256"]:
                raise QSXError("private egress configuration medium changed")
        else:
            nonce = uuid.uuid4().hex
            mac = "52:54:00:" + ":".join(nonce[n:n + 2] for n in (0, 2, 4))
            medium = instance.directory / f"egress-{nonce}.iso"
            marker_hash = make_seed(medium, instance.metadata["instance_id"], nonce, mac)
            pending = {
                "policy": admitted, "policy_sha256": digest,
                "policy_source": str(policy.resolve()), "nonce": nonce, "mac": mac,
                "medium": medium.name, "medium_sha256": sha256(medium),
                "marker_sha256": marker_hash, "revoked": False,
            }
            instance.metadata["egress_pending"] = pending
            _save(instance)
        domain = backend._domain(instance.name, inactive=True)
        matches = [disk for disk in domain.findall("./devices/disk")
                   if (source := disk.find("source")) is not None
                   and source.get("file") == str(medium)]
        if not matches:
            if any(disk.get("device") == "cdrom" for disk in domain.findall("./devices/disk")):
                raise QSXError("detach other configuration media before private egress import")
            backend.attach_readonly_medium(instance.name, medium)
        elif (len(matches) != 1 or matches[0].get("device") != "cdrom"
              or matches[0].find("readonly") is None):
            raise QSXError("private egress medium is not a unique readonly CD-ROM")
        (instance.directory / "domain.xml").write_text(backend.definition(instance.name))
        return {"name": instance.name, "network_profile": "offline",
                "status": "PRIVATE_CONFIGURATION_PREPARED", "medium": str(medium),
                "next_step": "review service-account storage access, start offline, then network-grant"}


def grant_network(path: Path | str, policy: Path) -> dict[str, Any]:
    """Explicitly grant one verified deployment, after guest configuration import."""
    from .runtime_egress import PROFILE, load_egress_policy, policy_digest, verify_network

    with _locked(path) as instance:
        admitted = load_egress_policy(policy, instance.metadata["instance_id"])
        pending = instance.metadata.get("egress_pending")
        if (instance.metadata["network_profile"] != "offline" or not pending
                or pending.get("revoked")
                or policy_digest(admitted) != pending["policy_sha256"]):
            raise QSXError("network-grant requires a current matching network-prepare import")
        backend = LibvirtBackend()
        verify_network(backend, instance.name)
        observation_path = instance.directory / f"egress-observation-{pending['nonce']}.json"
        if pending.get("observation_sha256"):
            if sha256(observation_path) != pending["observation_sha256"]:
                raise QSXError("saved private egress import observation changed")
        else:
            marker = "/var/lib/qsx-egress-configuration.json"
            script = (
                'test "$(id -un)" = agent && test "$(id -u)" -ne 0 && '
                f'test "$(sha256sum {marker} | cut -c 1-64)" = '
                + shlex.quote(pending["marker_sha256"]) + f" && cat {marker}"
            )
            observation = backend.run_guest(instance.name, ["sh", "-c", script], WORKSPACE, timeout=30)
            if observation["returncode"] != 0:
                raise QSXError("private egress configuration is not ready; inspect offline boot")
            atomic_json(observation_path, observation)
            pending["observation_sha256"] = sha256(observation_path)
            _save(instance)
        if backend.is_active(instance.name):
            backend.shutdown(instance.name)
        medium = instance.directory / pending["medium"]
        if sha256(medium) != pending["medium_sha256"]:
            raise QSXError("private egress configuration medium changed")
        backend.detach_medium(instance.name, medium, missing_ok=True)
        observed = backend.attach_egress(instance.name, pending["mac"])
        if instance.metadata.get("egress_grant"):
            instance.metadata.setdefault("egress_grant_history", []).append(instance.metadata["egress_grant"])
        instance.metadata["egress_grant"] = {
            "policy_source": str(policy.resolve()), "policy_sha256": policy_digest(admitted),
            "policy": admitted, "mac": pending["mac"], "boot_id": observed["boot_id"],
            "deployment_sha256": observed["deployment_sha256"], "granted_at_unix": time.time(),
        }
        instance.metadata.setdefault("egress_preparation_history", []).append(pending)
        del instance.metadata["egress_pending"]
        instance.metadata.update(network_profile=PROFILE, network_cut_latched=False, phase="stopped")
        (instance.directory / "domain.xml").write_text(backend.definition(instance.name))
        atomic_json(instance.directory / "egress-grant-observation.json", observed)
        _save(instance)
        return {"name": instance.name, "network_profile": PROFILE, "state": "shut off",
                "status": "EXPLICIT_GRANT_VERIFIED", "operator_login": "NOT_RUN"}
