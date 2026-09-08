"""Operator-owned task recipes and an offline, disposable Alpine image builder.

Only freeze performs network downloads. Recipe preparation is always executed in
QEMU, never by a host shell. Integrity hashes describe exact retained inputs, not
bit-identical image output or a security endorsement of a recipe.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from .build_output import DEFAULT_LOG_LIMIT_BYTES, log_contains, run_build
from .errors import QSXError
from .qcow import inspect_image
from .util import atomic_json, run, sha256

BASE = {
    "version": "3.24.1",
    "architecture": "x86_64",
    "url": "https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/cloud/"
    "generic_alpine-3.24.1-x86_64-bios-cloudinit-r0.qcow2",
    "sha512": "8d756f6fc7653daa4fb4e2e213d8a66007bcb1e5a846e28891af62c47b90685c"
    "694486c2746099ad99e9e8f5278db76b69d11dfe1e9361aa4c8406df16929a9c",
}
CODEX = {
    "enabled": True,
    "version": "0.153.4",
    "url": "https://github.com/openai/codex/releases/download/rust-v0.153.4/"
    "codex-package-x86_64-unknown-linux-musl.tar.gz",
    "sha256": "a822187e1a2420c61c5926721bfbd878701ed95547c9bb0d4de4498a16ba1821",
}
MINIMUM_PACKAGES = [
    "bash",
    "git",
    "tar",
    "coreutils",
    "mount",
    "shadow",
    "cloud-utils-growpart",
    "acpid",
]
RESOURCE_DEFAULTS = {"memory_mib": 2048, "vcpus": 2, "disk_gib": 8}
_FIELDS = {
    "schema_version",
    "name",
    "description",
    "extends",
    "base",
    "agent_profile",
    "packages",
    "resources",
    "prepare",
    "readiness",
    "files",
    "artifacts",
}
_NAME = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_PACKAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9+_.-]*(?:=[a-zA-Z0-9+_.~-]+)?\Z")


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(v, str) or not v or "\0" in v for v in value
    ):
        raise QSXError(f"{field} must be a list of nonempty strings without NUL")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise QSXError(f"cannot read JSON manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise QSXError(f"manifest must be an object: {path}")
    return value


def _https(url: Any, field: str) -> None:
    if not isinstance(url, str):
        raise QSXError(f"{field} must be an HTTPS URL")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise QSXError(f"{field} must be an HTTPS URL without credentials or fragment")


def _merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    result = dict(parent)
    for key, value in child.items():
        if key in {"base", "codex", "resources"}:
            if not isinstance(value, dict):
                raise QSXError(f"{key} must be a table")
            result[key] = {**result.get(key, {}), **value}
        elif key in {"packages", "prepare", "readiness"}:
            result[key] = result.get(key, []) + _strings(value, key)
        elif key == "artifacts":
            own = _validated_artifacts(value, reject_duplicates=True)
            result[key] = _validated_artifacts(result.get(key, []) + own)
        elif key == "files":
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise QSXError("files must be an array of tables")
            result[key] = result.get(key, []) + value
        elif key != "extends":
            result[key] = value
    return result


def _validated_artifacts(value: Any, *, reject_duplicates: bool = False) -> list[dict[str, str]]:
    """Opaque downloads only; identical names explicitly replace inherited data."""
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise QSXError("artifacts must be an array of tables")
    result: dict[str, dict[str, str]] = {}
    for item in value:
        if set(item) != {"name", "url", "sha256"}:
            raise QSXError("artifact requires exactly name, url and sha256")
        name = item["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
            raise QSXError("artifact name must be a safe ASCII basename, at most 128 characters")
        _https(item["url"], "artifact.url")
        if not isinstance(item["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
            raise QSXError("artifact.sha256 must be a lowercase SHA-256 digest")
        if name in result and reject_duplicates:
            raise QSXError(
                "duplicate artifact name within one file; inheritance overrides are explicit"
            )
        result[name] = {"name": name, "url": item["url"], "sha256": item["sha256"]}
    return [result[name] for name in sorted(result)]


def _freeze_artifacts(recipe: dict[str, Any], cache: Path) -> list[dict[str, Any]]:
    """Fetch opaque inputs without extracting archives or executing their content."""
    result: list[dict[str, Any]] = []
    for spec in recipe.get("artifacts", []):
        path = cache / "blobs" / (spec["sha256"] + ".artifact")
        record = _download(spec["url"], path, "sha256", spec["sha256"])
        record.update(
            name=spec["name"],
            path=str(path.relative_to(cache)),
            guest_path="/mnt/qsx-build/artifacts/" + spec["name"],
            kind="recipe-artifact",
        )
        result.append(record)
    return result


def _validated_files(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise QSXError("files must be an array of tables")
    files: dict[str, dict[str, Any]] = {}
    for item in value:
        if set(item) - {"path", "content", "mode"}:
            raise QSXError("unknown file field")
        target = item.get("path")
        if (
            not isinstance(target, str)
            or not target.startswith("/")
            or "\0" in target
            or ".." in PurePosixPath(target).parts
        ):
            raise QSXError("file.path must be an absolute guest path without traversal")
        if not target.startswith(("/etc/", "/usr/local/", "/opt/")):
            raise QSXError(
                "configuration files belong under /etc, /usr/local or /opt; no project/auth content"
            )
        if not isinstance(item.get("content"), str) or "\0" in item["content"]:
            raise QSXError("file.content must be text without NUL")
        mode = item.get("mode", "0644")
        if not isinstance(mode, str) or not re.fullmatch(r"0[0-7]{3}", mode):
            raise QSXError("file.mode must be octal text without special permission bits")
        files[target] = {"path": target, "content": item["content"], "mode": mode}
    return [files[key] for key in sorted(files)]


def load_agent_profile(path: Path | str) -> dict[str, Any]:
    """Read an operator-selected guest agent profile; it cannot set host policy."""
    try:
        profile = tomllib.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise QSXError(f"invalid agent profile {path}: {exc}") from exc
    fields = {
        "schema_version",
        "name",
        "description",
        "packages",
        "codex",
        "files",
        "prepare",
        "readiness",
        "argv",
        "artifacts",
    }
    if set(profile) - fields:
        raise QSXError(
            "unknown agent profile field; profiles cannot grant resources or host policy"
        )
    if type(profile.get("schema_version")) is not int or profile["schema_version"] != 1:
        raise QSXError("agent profile schema_version must be 1")
    if not isinstance(profile.get("name"), str) or not _NAME.fullmatch(profile["name"]):
        raise QSXError("invalid agent profile name")
    if not isinstance(profile.get("description", ""), str):
        raise QSXError("agent profile description must be a string")
    profile["description"] = profile.get("description", "")
    for field in ("packages", "prepare", "readiness"):
        profile[field] = _strings(profile.get(field, []), f"agent profile {field}")
    if any(not _PACKAGE.fullmatch(package) for package in profile["packages"]):
        raise QSXError("invalid agent profile package specification")
    profile["packages"] = sorted(set(profile["packages"]))
    argv = profile.get("argv")
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 64
        or any(not isinstance(arg, str) or "\0" in arg or len(arg) > 4096 for arg in argv)
        or not argv[0]
    ):
        raise QSXError("agent profile argv must be a bounded nonempty argument vector without NUL")
    if any(arg in {"--dangerously-bypass-approvals-and-sandbox", "--yolo"} for arg in argv):
        raise QSXError("agent profile may not disable the client sandbox with bypass flags")
    codex = profile.get("codex", {"enabled": False})
    if not isinstance(codex, dict) or set(codex) - set(CODEX):
        raise QSXError("agent profile codex must be a table of pinned tool settings")
    profile["codex"] = {**CODEX, **codex}
    if type(profile["codex"]["enabled"]) is not bool:
        raise QSXError("codex.enabled must be boolean")
    if {k: v for k, v in profile["codex"].items() if k != "enabled"} != {
        k: v for k, v in CODEX.items() if k != "enabled"
    }:
        raise QSXError("publisher verification is pinned to the supported official Codex release")
    profile["files"] = _validated_files(profile.get("files", []))
    profile["artifacts"] = _validated_artifacts(
        profile.get("artifacts", []), reject_duplicates=True
    )
    profile["profile_sha256"] = _digest(profile)
    return profile


def load_recipe(path: Path | str) -> dict[str, Any]:
    """Resolve one relative extends chain and validate it, without creating files."""

    def read(source: Path, stack: tuple[Path, ...]) -> dict[str, Any]:
        source = source.resolve()
        if source in stack or len(stack) >= 16:
            raise QSXError("recipe inheritance cycle or depth greater than 16")
        try:
            raw = tomllib.loads(source.read_text())
        except (OSError, ValueError) as exc:
            raise QSXError(f"invalid recipe {source}: {exc}") from exc
        if set(raw) - _FIELDS:
            raise QSXError(
                f"unknown recipe fields: {sorted(set(raw) - _FIELDS)}; recipes cannot set host policy"
            )
        parent: dict[str, Any] = {}
        if "extends" in raw:
            extends = raw["extends"]
            if not isinstance(extends, str) or not extends or Path(extends).is_absolute():
                raise QSXError("extends must be one relative recipe path")
            parent = read(source.parent / extends, (*stack, source))
        if "agent_profile" in raw:
            profile_path = raw["agent_profile"]
            if (
                not isinstance(profile_path, str)
                or not profile_path
                or Path(profile_path).is_absolute()
            ):
                raise QSXError("agent_profile must name one relative profile file")
            raw["agent_profile"] = load_agent_profile(source.parent / profile_path)
        return _merge(parent, raw)

    recipe = read(Path(path), ())
    if type(recipe.get("schema_version")) is not int or recipe["schema_version"] != 1:
        raise QSXError("recipe schema_version must be 1")
    if not isinstance(recipe.get("name"), str) or not _NAME.fullmatch(recipe["name"]):
        raise QSXError("invalid recipe name")
    if not isinstance(recipe.get("description", ""), str):
        raise QSXError("description must be a string")
    recipe["description"] = recipe.get("description", "")
    recipe["base"] = {**BASE, **recipe.get("base", {})}
    if "agent_profile" not in recipe:
        raise QSXError("recipe must explicitly select an agent_profile file")
    profile = recipe["agent_profile"]
    recipe["codex"] = profile["codex"]
    for field in ("packages", "prepare", "readiness", "files", "artifacts"):
        recipe[field] = profile[field] + recipe.get(field, [])
    recipe["agent_argv"] = profile["argv"]
    recipe["agent_profile_sha256"] = profile["profile_sha256"]
    for section, fields in (("base", set(BASE)), ("codex", set(CODEX))):
        value = recipe[section]
        if set(value) - fields:
            raise QSXError(f"unknown {section} fields")
        _https(value["url"], f"{section}.url")
        hash_field, size = ("sha512", 128) if section == "base" else ("sha256", 64)
        if not isinstance(value[hash_field], str) or not re.fullmatch(
            f"[a-f0-9]{{{size}}}", value[hash_field]
        ):
            raise QSXError(f"{section}.{hash_field} must be a lowercase hex digest")
        if not isinstance(value["version"], str) or not re.fullmatch(
            r"[0-9]+(?:\.[0-9]+){2}", value["version"]
        ):
            raise QSXError(f"{section}.version must be an exact release version")
        if value["version"] not in value["url"]:
            raise QSXError(f"{section}.url must contain the pinned version")
    if recipe["base"]["architecture"] != "x86_64" or recipe["base"]["version"] != BASE["version"]:
        raise QSXError(
            "this builder supports Alpine 3.24.1 x86_64; APK trust/runtime is pinned to it"
        )
    if not isinstance(recipe["codex"]["enabled"], bool):
        raise QSXError("codex.enabled must be boolean")
    packages = _strings(recipe.get("packages", []), "packages")
    if any(not _PACKAGE.fullmatch(package) for package in packages):
        raise QSXError("packages must contain APK names or exact name=version specifications")
    if recipe["base"] != BASE:
        raise QSXError("publisher verification is pinned to the supported official Alpine base")
    if {k: v for k, v in recipe["codex"].items() if k != "enabled"} != {
        k: v for k, v in CODEX.items() if k != "enabled"
    }:
        raise QSXError("publisher verification is pinned to the supported official Codex release")
    recipe["packages"] = sorted(set(MINIMUM_PACKAGES + packages))
    resources = {**RESOURCE_DEFAULTS, **recipe.get("resources", {})}
    if set(resources) != set(RESOURCE_DEFAULTS):
        raise QSXError("unknown resource field")
    for key, upper in (("memory_mib", 1048576), ("vcpus", 256), ("disk_gib", 65536)):
        if type(resources[key]) is not int or not 1 <= resources[key] <= upper:
            raise QSXError(f"resources.{key} is outside schema bounds")
    if resources["memory_mib"] < 256 or resources["disk_gib"] < 2:
        raise QSXError("Alpine cloud guest needs at least 256 MiB memory and 2 GiB disk")
    recipe["resources"] = resources
    for field in ("prepare", "readiness"):
        recipe[field] = _strings(recipe.get(field, []), field)
    recipe["files"] = _validated_files(recipe.get("files", []))
    recipe["artifacts"] = _validated_artifacts(recipe.get("artifacts", []))
    recipe["recipe_sha256"] = _digest(recipe)
    return recipe


def list_templates(directory: Path | str) -> list[dict[str, Any]]:
    return [
        {"path": str(path), **load_recipe(path)} for path in sorted(Path(directory).glob("*.toml"))
    ]


def create_recipe(
    path: Path | str,
    name: str,
    packages: list[str] | None = None,
    agent_profile: Path | str | None = None,
) -> dict[str, Any]:
    """Create a task recipe referencing an explicit profile, without overwriting."""
    target = Path(path)
    if not _NAME.fullmatch(name):
        raise QSXError("invalid recipe name")
    packages = packages or []
    if any(not isinstance(p, str) or not _PACKAGE.fullmatch(p) for p in packages):
        raise QSXError("invalid package specification")
    profile_path = (
        Path(agent_profile)
        if agent_profile is not None
        else Path(__file__).resolve().parents[2] / "agent-profiles" / "codex.toml"
    )
    load_agent_profile(profile_path)
    relative_profile = os.path.relpath(profile_path.resolve(), target.parent.resolve())
    content = f"schema_version = 1\nname = {json.dumps(name)}\nagent_profile = {json.dumps(relative_profile)}\npackages = {json.dumps(packages)}\n"
    content += (
        'readiness = ["git --version"]\n\n[resources]\nmemory_mib = 2048\nvcpus = 2\ndisk_gib = 8\n'
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x") as stream:
            stream.write(content)
    except FileExistsError as exc:
        raise QSXError("refusing to overwrite a recipe") from exc
    return load_recipe(target)


def _implementation() -> dict[str, str]:
    return {
        name: sha256(Path(__file__).with_name(name))
        for name in ("templates.py", "apk_cache.py", "provenance.py", "util.py", "config.py",
                     "qcow.py", "build_output.py")
    }


def _download(url: str, target: Path, algorithm: str, expected: str) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not target.exists():
        temporary = target.with_name(target.name + ".download-" + uuid.uuid4().hex)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "QSX-input-freeze/1"})
            with (
                urllib.request.urlopen(request, timeout=120) as response,
                temporary.open("xb") as stream,
            ):
                if urllib.parse.urlsplit(response.url).scheme != "https":
                    raise QSXError("refusing download redirect away from HTTPS")
                shutil.copyfileobj(response, stream)
            if _file_digest(temporary, algorithm) != expected:
                raise QSXError(f"download {algorithm} mismatch: {url}")
            temporary.replace(target)
        except (OSError, ValueError) as exc:
            raise QSXError(f"input download failed: {url}: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
    if target.is_symlink() or not target.is_file() or _file_digest(target, algorithm) != expected:
        raise QSXError(f"cached input integrity failure: {target}")
    return {
        "url": url,
        algorithm: expected,
        "sha256": sha256(target),
        "size_bytes": target.stat().st_size,
    }


def _file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(root: Path, relative: Any) -> Path:
    if (
        not isinstance(relative, str)
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise QSXError("invalid relative artifact path")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise QSXError("artifact escapes cache or is a symbolic link")
    return path


def _verify_lock(lock: dict[str, Any], cache: Path) -> None:
    if lock.get("schema_version") != 1 or lock.get("lock_sha256") != _digest(
        {k: v for k, v in lock.items() if k != "lock_sha256"}
    ):
        raise QSXError("input lock integrity failure")
    required = {
        "recipe",
        "implementation_sha256",
        "artifacts",
        "base_path",
        "codex_path",
        "apk_dir",
        "packages",
        "provenance",
    }
    if not required <= set(lock) or not isinstance(lock["artifacts"], list):
        raise QSXError("input lock schema is incomplete")
    if lock.get("implementation_sha256") != _implementation():
        raise QSXError("builder/helper code changed; run explicit freeze for a new lock")
    for artifact in lock["artifacts"]:
        if not isinstance(artifact, dict) or not {"path", "size_bytes", "sha256"} <= set(artifact):
            raise QSXError("input artifact manifest is incomplete")
        path = _inside(cache, artifact["path"])
        if (
            not path.is_file()
            or path.stat().st_size != artifact["size_bytes"]
            or sha256(path) != artifact["sha256"]
        ):
            raise QSXError(f"missing or corrupt frozen input: {artifact['path']}")


def freeze_recipe(path: Path | str, cache: Path | str, refresh: bool = False) -> Path:
    """Freeze all bytes online, or verify/reuse a complete immutable input lock."""
    from .apk_cache import fetch_packages
    from .provenance import verify_inputs

    recipe, cache = load_recipe(path), Path(cache).resolve()
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (cache / ".freeze.lock").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        reference_key = _digest(
            {"recipe_sha256": recipe["recipe_sha256"], "implementation_sha256": _implementation()}
        )
        ref = cache / "recipes" / (reference_key + ".json")
        if ref.exists() and not refresh:
            lock_path = _inside(cache, _read_json(ref)["lock"])
            _verify_lock(_read_json(lock_path), cache)
            return lock_path
        artifacts: list[dict[str, Any]] = _freeze_artifacts(recipe, cache)
        base = recipe["base"]
        base_path = cache / "blobs" / (base["sha512"] + ".qcow2")
        base_artifact = _download(base["url"], base_path, "sha512", base["sha512"])
        base_artifact.update(path=str(base_path.relative_to(cache)), kind="base")
        artifacts.append(base_artifact)
        codex_path = None
        if recipe["codex"]["enabled"]:
            codex = recipe["codex"]
            codex_path = cache / "blobs" / (codex["sha256"] + ".tar.gz")
            item = _download(codex["url"], codex_path, "sha256", codex["sha256"])
            item.update(path=str(codex_path.relative_to(cache)), kind="codex")
            artifacts.append(item)
        proof_root = cache / "proofs" / _digest({"base": base, "codex": recipe["codex"]})
        proof_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        provenance = verify_inputs(base_path, codex_path, proof_root, offline=False)
        for proof in provenance["artifacts"]:
            artifacts.append(
                {
                    **proof,
                    "path": str((proof_root / proof["path"]).relative_to(cache)),
                    "kind": "publisher-proof",
                }
            )
        package_key = _digest({"release": base["version"], "packages": recipe["packages"]})
        apk_dir = cache / "apk" / (package_key + ("-" + uuid.uuid4().hex if refresh else ""))
        package_lock = fetch_packages(apk_dir, recipe["packages"], offline=False)
        for entry in package_lock["artifacts"]:
            artifacts.append(
                {
                    **entry,
                    "path": str((apk_dir / entry["path"]).relative_to(cache)),
                    "kind": "apk-input",
                }
            )
        apk_lock_path = apk_dir / "lock.json"
        artifacts.append(
            {
                "path": str(apk_lock_path.relative_to(cache)),
                "sha256": sha256(apk_lock_path),
                "size_bytes": apk_lock_path.stat().st_size,
                "kind": "apk-lock",
            }
        )
        lock = {
            "schema_version": 1,
            "recipe": recipe,
            "implementation_sha256": _implementation(),
            "base_path": str(base_path.relative_to(cache)),
            "codex_path": str(codex_path.relative_to(cache)) if codex_path else None,
            "apk_dir": str(apk_dir.relative_to(cache)),
            "packages": package_lock["packages"],
            "artifacts": artifacts,
            "provenance": provenance,
        }
        lock["lock_sha256"] = _digest(lock)
        lock_path = cache / "locks" / (str(lock["lock_sha256"]) + ".json")
        if not lock_path.exists():
            atomic_json(lock_path, lock)
        _verify_lock(lock, cache)
        atomic_json(ref, {"lock": str(lock_path.relative_to(cache))})
        return lock_path


def _guest_script(recipe: dict[str, Any], marker: str) -> str:
    """Generate guest-only root setup, followed by non-root readiness checks."""
    lines = [
        "#!/bin/sh",
        "set -eu",
        "exec >/dev/ttyS0 2>&1",
        "trap 'echo QSX_BUILD_FAILED; sync; poweroff -f' EXIT",
        'test "$(id -u)" = 0',
        "mkdir -p /mnt/qsx-build",
        "mountpoint -q /mnt/qsx-build || mount -o ro LABEL=cidata /mnt/qsx-build",
        "apk add --no-network /mnt/qsx-build/packages/*.apk",
        "id agent >/dev/null 2>&1 || adduser -D -s /bin/bash agent",
        "passwd -l root",
        "passwd -l agent",
        "mkdir -p /workspace/project /home/agent/.codex /etc/qsx /usr/local/sbin /usr/local/bin",
        "chown agent:agent /workspace/project /home/agent/.codex",
        "chmod 700 /home/agent/.codex",
    ]
    if recipe["codex"]["enabled"]:
        lines += [
            "mkdir -p /opt/qsx-codex",
            "tar -xzf /mnt/qsx-build/codex.tar.gz -C /opt/qsx-codex --no-same-owner --no-same-permissions",
            "codex_binary=$(find /opt/qsx-codex -type f \\( -name codex -o -name 'codex-x86_64-unknown-linux-musl' \\) | head -n 1)",
            'test -n "$codex_binary"; chmod 755 "$codex_binary"; ln -s "$codex_binary" /usr/local/bin/codex',
            "printf '%s\\n' 'sandbox_mode = \"workspace-write\"' 'approval_policy = \"on-request\"' '[sandbox_workspace_write]' 'network_access = false' > /home/agent/.codex/config.toml",
            "chown agent:agent /home/agent/.codex/config.toml; chmod 600 /home/agent/.codex/config.toml",
        ]
    # root receives a local PTY from init, and immediately changes to the agent UID.
    console = "#!/bin/sh\nexec </dev/ttyS0 >/dev/ttyS0 2>&1\nexport TERM=vt100\nexec su - agent\n"
    config = "datasource_list: [ NoCloud ]\ngrowpart:\n  mode: auto\n  devices: ['/']\nresize_rootfs: true\n"
    builtins = [
        {"path": "/usr/local/sbin/qsx-console", "content": console, "mode": "0755"},
        {"path": "/etc/cloud/cloud.cfg.d/99-qsx.cfg", "content": config, "mode": "0644"},
    ]
    for item in [*recipe["files"], *builtins]:
        payload = base64.b64encode(item["content"].encode()).decode()
        target = "'" + item["path"].replace("'", "'\"'\"'") + "'"
        lines += [
            f'mkdir -p "$(dirname {target})"',
            f"printf '%s' '{payload}' | base64 -d > {target}",
            f"chmod {item['mode']} {target}",
        ]
    lines += recipe["prepare"]
    lines += [
        "sed -i '/^ttyS0:/d' /etc/inittab",
        "echo 'ttyS0::respawn:/usr/local/sbin/qsx-console' >> /etc/inittab",
        "rc-update add acpid default",
        "rc-update del sshd default 2>/dev/null || true",
        "rm -f /etc/ssh/ssh_host_* /root/.ssh/authorized_keys /home/alpine/.ssh/authorized_keys",
        "rm -rf /etc/sudoers.d/* /etc/doas.d/*",
        "printf '%s\\n' 'root ALL=(ALL:ALL) ALL' > /etc/sudoers",
        "chmod 440 /etc/sudoers",
        "id alpine >/dev/null 2>&1 && passwd -l alpine || true",
        "apk info -vv > /etc/qsx/packages.txt",
    ]
    checks = [
        'test "$(id -u)" -ne 0',
        "git --version",
        "command -v " + shlex.quote(recipe["agent_argv"][0]) + " >/dev/null",
    ]
    if recipe["codex"]["enabled"]:
        checks += [
            "codex --version",
            "codex login --help | grep -q -- --device-auth",
            "codex exec --help >/dev/null",
        ]
    checks += recipe["readiness"]
    readiness = "set -eu\ncd /workspace/project\n" + "\n".join(checks) + "\n"
    encoded = base64.b64encode(readiness.encode()).decode()
    lines += [
        f"printf '%s' '{encoded}' | base64 -d > /tmp/qsx-readiness.sh",
        "chmod 755 /tmp/qsx-readiness.sh; su -s /bin/sh agent -c /tmp/qsx-readiness.sh",
        "rm -f /tmp/qsx-readiness.sh",
        "test ! -e /home/agent/.codex/auth.json; test ! -e /root/.codex/auth.json",
        "cloud-init clean --logs",
        "rm -rf /var/lib/cloud/*",
        "rm -f /etc/machine-id /var/lib/dbus/machine-id",
        "rm -f /root/.ash_history /home/agent/.bash_history /var/lib/qsx-import-complete /var/lib/qsx-instance-id",
        "sync",
        f"echo '{marker}'",
        "trap - EXIT",
        "poweroff",
    ]
    return "\n".join(lines) + "\n"


def _make_seed(lock: dict[str, Any], cache: Path, directory: Path, marker: str) -> Path:
    seed = directory / "seed"
    seed.mkdir()
    (seed / "packages").mkdir()
    (seed / "artifacts").mkdir()
    for entry in lock.get("artifacts", []):
        if entry.get("kind") == "recipe-artifact":
            _validated_artifacts([{key: entry[key] for key in ("name", "url", "sha256")}])
            shutil.copy2(_inside(cache, entry["path"]), seed / "artifacts" / entry["name"])
    apk_dir = _inside(cache, lock["apk_dir"])
    for entry in lock["packages"]:
        source = _inside(apk_dir, entry["path"])
        shutil.copy2(source, seed / "packages" / source.name)
    if lock["codex_path"]:
        shutil.copy2(_inside(cache, lock["codex_path"]), seed / "codex.tar.gz")
    (seed / "build.sh").write_text(_guest_script(lock["recipe"], marker))
    (seed / "meta-data").write_text(
        "instance-id: qsx-build-" + uuid.uuid4().hex + "\nlocal-hostname: qsx-template\n"
    )
    (seed / "user-data").write_text(
        "#cloud-config\nssh_deletekeys: true\nssh_genkeytypes: []\n"
        "growpart: {mode: auto, devices: ['/']}\nresize_rootfs: true\n"
        "runcmd:\n  - [mkdir, -p, /mnt/qsx-build]\n"
        "  - [mount, -o, ro, 'LABEL=cidata', /mnt/qsx-build]\n"
        "  - [sh, /mnt/qsx-build/build.sh]\n"
    )
    medium = directory / "seed.iso"
    run(
        [
            "xorriso",
            "-as",
            "mkisofs",
            "-quiet",
            "-J",
            "-R",
            "-V",
            "cidata",
            "-o",
            str(medium),
            str(seed),
        ]
    )
    return medium


def _check_qcow(path: Path) -> dict[str, Any]:
    return inspect_image(path)


def verify_image(manifest_path: Path | str) -> dict[str, Any]:
    path = Path(manifest_path)
    manifest = _read_json(path)
    if manifest.get("schema_version") != 1 or manifest.get("manifest_sha256") != _digest(
        {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    ):
        raise QSXError("image manifest integrity failure")
    required = {
        "image",
        "image_sha256",
        "size_bytes",
        "boot_log",
        "boot_log_sha256",
        "readiness_marker",
    }
    if not required <= set(manifest):
        raise QSXError("image manifest schema is incomplete")
    image = _inside(path.parent, manifest["image"])
    if (
        not image.is_file()
        or image.stat().st_size != manifest["size_bytes"]
        or sha256(image) != manifest["image_sha256"]
    ):
        raise QSXError("image SHA-256 or size mismatch")
    log = _inside(path.parent, manifest["boot_log"])
    if not log.is_file() or sha256(log) != manifest["boot_log_sha256"]:
        raise QSXError("boot evidence hash mismatch")
    if not log_contains(log, manifest["readiness_marker"]):
        raise QSXError("guest readiness evidence missing")
    _check_qcow(image)
    return manifest


def _publish_image(
    source: Path, log: Path, output: Path, manifest: dict[str, Any], cache_hit: bool
) -> dict[str, Any]:
    publish_started = time.monotonic()
    if output.exists() or output.with_suffix(output.suffix + ".manifest.json").exists():
        raise QSXError("refusing to overwrite an image or manifest; use a new output path")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_log = output.with_suffix(output.suffix + ".boot.log")
    if target_log.exists():
        raise QSXError("refusing to overwrite a boot evidence log")
    try:
        shutil.copy2(source, output)
        shutil.copy2(log, target_log)
        _check_qcow(output)
        output.chmod(0o444)
        result = {
            **manifest,
            "image": output.name,
            "image_sha256": sha256(output),
            "size_bytes": output.stat().st_size,
            "allocated_bytes": output.stat().st_blocks * 512,
            "cache_hit": cache_hit,
            "boot_log": target_log.name,
            "boot_log_sha256": sha256(target_log),
        }
        result["publish_seconds"] = round(time.monotonic() - publish_started, 3)
        result.pop("manifest_sha256", None)
        result["manifest_sha256"] = _digest(result)
        atomic_json(output.with_suffix(output.suffix + ".manifest.json"), result)
        return result
    except BaseException:
        output.unlink(missing_ok=True)
        target_log.unlink(missing_ok=True)
        raise


@contextmanager
def _build_workspace(cached: Path) -> Iterator[Path]:
    """Keep a failed attempt's disk, seed and log for diagnosis; never reuse it."""
    work = Path(tempfile.mkdtemp(prefix="qsx-build-", dir=cached))
    # Exceptions from the caller propagate at yield, keeping this unique
    # attempt intact. Only a completed build authorizes removal of scratch data.
    yield work
    shutil.rmtree(work)


def build_image(
    lock: Path | str,
    cache: Path | str,
    output: Path | str,
    accelerator: str = "kvm",
    timeout: int = 1800,
    policy: Path | None = None,
    max_log_bytes: int = DEFAULT_LOG_LIMIT_BYTES,
) -> dict[str, Any]:
    """Boot an offline build guest, require actual readiness, seal independent QCOW2.

    Recipe resource values are requests, not grants. The operator controls this
    builder invocation and the separate instance policy. TCG is opt-in only.
    """
    from .apk_cache import fetch_packages
    from .config import load_policy, resolve_resources

    if accelerator not in {"kvm", "tcg"}:
        raise QSXError("accelerator must be kvm or explicit tcg")
    if type(timeout) is not int or timeout <= 0:
        raise QSXError("build timeout must be a positive integer")
    if type(max_log_bytes) is not int or max_log_bytes <= 0:
        raise QSXError("build log limit must be a positive integer")
    cache, output = Path(cache).resolve(), Path(output).resolve()
    data = _read_json(Path(lock))
    _verify_lock(data, cache)
    operator_policy = load_policy(policy)
    resources = resolve_resources(data["recipe"]["resources"], None, operator_policy)
    fetch_packages(_inside(cache, data["apk_dir"]), data["recipe"]["packages"], offline=True)
    versions = {
        tool: run([tool, "--version"]).splitlines()[0]
        for tool in ("qemu-img", "qemu-system-x86_64", "xorriso")
    }
    tool_hashes = {tool: sha256(Path(shutil.which(tool) or tool)) for tool in versions}
    key = _digest(
        {
            "lock": data["lock_sha256"],
            "versions": versions,
            "tool_sha256": tool_hashes,
            "accelerator": accelerator,
        }
    )
    cached = cache / "images" / key
    cached.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (cached / ".build.lock").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        cached_image, cached_manifest = cached / "image.qcow2", cached / "image.qcow2.manifest.json"
        started = time.monotonic()
        if cached_manifest.exists():
            manifest = verify_image(cached_manifest)
            if (cached / manifest["boot_log"]).stat().st_size > max_log_bytes:
                raise QSXError("cached build log exceeds the selected host log limit")
            return _publish_image(
                cached_image, cached / manifest["boot_log"], output, manifest, True
            )
        if cached_image.exists():
            raise QSXError(
                "incomplete image cache; preserve evidence and use a new cache directory"
            )
        if accelerator == "kvm" and not os.access("/dev/kvm", os.R_OK | os.W_OK):
            raise QSXError(
                "KVM is unavailable; no automatic TCG fallback (use explicit --accelerator tcg for testing)"
            )
        with _build_workspace(cached) as work:
            disk = work / "disk.qcow2"
            source = _inside(cache, data["base_path"])
            source_info = _check_qcow(source)
            if resources["disk_gib"] * 1024**3 < source_info["virtual-size"]:
                raise QSXError("requested image disk would shrink the Alpine base")
            run(["qemu-img", "convert", "-f", "qcow2", "-O", "qcow2", str(source), str(disk)])
            run(["qemu-img", "resize", "-f", "qcow2", str(disk), f"{resources['disk_gib']}G"])
            marker = "QSX_BUILD_READY_" + uuid.uuid4().hex
            medium = _make_seed(data, cache, work, marker)
            boot_log = work / "build.boot.log"
            qemu = [
                "qemu-system-x86_64",
                "-machine",
                "q35",
                "-accel",
                accelerator,
                "-m",
                str(resources["memory_mib"]),
                "-smp",
                str(resources["vcpus"]),
                "-display",
                "none",
                "-monitor",
                "none",
                "-serial",
                "stdio",
                "-nic",
                "none",
                "-no-reboot",
                "-drive",
                f"file={str(disk).replace(chr(44), chr(44) * 2)},format=qcow2,if=virtio",
                "-drive",
                f"file={str(medium).replace(chr(44), chr(44) * 2)},format=raw,media=cdrom,readonly=on",
            ]
            boot_started = time.monotonic()
            returncode = run_build(qemu, boot_log, timeout, max_log_bytes)
            boot_seconds = time.monotonic() - boot_started
            if returncode != 0 or not log_contains(boot_log, marker):
                raise QSXError(
                    f"build guest failed readiness (exit {returncode}); evidence: {boot_log}"
                )
            _check_qcow(disk)
            run(
                [
                    "qemu-img",
                    "convert",
                    "-c",
                    "-f",
                    "qcow2",
                    "-O",
                    "qcow2",
                    str(disk),
                    str(cached_image),
                ]
            )
            info = _check_qcow(cached_image)
            cached_image.chmod(0o444)
            retained_log = cached / "build.boot.log"
            shutil.copy2(boot_log, retained_log)
            boot_log = retained_log
            manifest = {
                "schema_version": 1,
                "image": cached_image.name,
                "image_sha256": sha256(cached_image),
                "size_bytes": cached_image.stat().st_size,
                "allocated_bytes": cached_image.stat().st_blocks * 512,
                "virtual_size_bytes": info["virtual-size"],
                "standalone": True,
                "recipe": data["recipe"],
                "recipe_sha256": data["recipe"]["recipe_sha256"],
                "agent_profile_sha256": data["recipe"]["agent_profile_sha256"],
                "agent_argv": data["recipe"]["agent_argv"],
                "additional_artifacts": [
                    entry for entry in data["artifacts"] if entry.get("kind") == "recipe-artifact"
                ],
                "lock_sha256": data["lock_sha256"],
                "build_key": key,
                "resources": resources,
                "operator_policy_at_build": operator_policy,
                "tool_versions": versions,
                "tool_sha256": tool_hashes,
                "packages": data["packages"],
                "accelerator": accelerator,
                "boot_log": boot_log.name,
                "boot_log_sha256": sha256(boot_log),
                "readiness_marker": marker,
                "boot_seconds": round(boot_seconds, 3),
                "build_log_limit_bytes": max_log_bytes,
                "build_seconds": round(time.monotonic() - started, 3),
                "cache_hit": False,
                "evidence": "actual_guest_readiness_and_clean_shutdown",
                "host_validation": "NOT_RUN",
                "operator_auth": "NOT_RUN",
                "bit_reproducibility": "NOT_CLAIMED",
            }
            manifest["manifest_sha256"] = _digest(manifest)
            atomic_json(cached_manifest, manifest)
            return _publish_image(cached_image, boot_log, output, manifest, False)
