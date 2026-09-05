from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import QSXError

SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
SAFE_TASK = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


@dataclass(frozen=True)
class Config:
    name: str
    source: Path
    state_dir: Path
    disk: Path
    disk_gib: int = 60
    network_profile: str = "offline"
    branch_prefix: str = "agent/mathresearch"
    guest_workspace: str = "/home/agent/workspace/PROJECT"
    base_image: Path | None = None
    base_image_sha256: str | None = None
    base_image_source: str | None = None
    base_image_release: str | None = None


def load(path: Path) -> Config:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "name", "source", "state_dir", "disk"}
    missing = required - data.keys()
    if missing:
        raise QSXError(f"missing configuration keys: {', '.join(sorted(missing))}")
    if data["schema_version"] != 1 or not SAFE_NAME.fullmatch(data["name"]):
        raise QSXError("unsupported schema or unsafe environment name")
    source = Path(data["source"]).expanduser().resolve()
    state = Path(data["state_dir"]).expanduser().resolve()
    disk = Path(data["disk"]).expanduser().resolve()
    if not source.is_absolute() or source == Path("/") or state == Path("/"):
        raise QSXError("source/state paths must be safe absolute paths")
    if any(
        private == source or private.is_relative_to(source) or source.is_relative_to(private)
        for private in (state, disk)
    ):
        raise QSXError("source, state and disk boundaries must not overlap")
    if disk == state or state.is_relative_to(disk):
        raise QSXError("disk cannot be the state directory or contain it")
    profile = data.get("network_profile", "offline")
    if profile not in {"offline", "openai"}:
        raise QSXError("network_profile must be offline or openai")
    git = data.get("git", {})
    if git.get("source_read_only", True) is not True or git.get("allow_push", False):
        raise QSXError("source_read_only=true and allow_push=false are mandatory")
    disk_gib = data.get("disk_gib", 60)
    if disk_gib != 60:
        raise QSXError("MathResearch MVP requires a 60 GiB private disk")
    guest_workspace = data.get("workspace", "/home/agent/workspace/PROJECT")
    if not isinstance(guest_workspace, str) or not re.fullmatch(
        r"/home/agent/workspace/[A-Za-z0-9._-]+", guest_workspace
    ):
        raise QSXError("guest workspace must be below /home/agent/workspace")
    image = data.get("base_image", {})
    base_path = Path(image["path"]).expanduser().resolve() if image.get("path") else None
    base_hash = image.get("sha256")
    base_source = image.get("source")
    base_release = image.get("release")
    if base_path and (
        not isinstance(base_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", base_hash)
    ):
        raise QSXError("base image requires a lowercase SHA-256")
    if base_path and (not isinstance(base_source, str) or not isinstance(base_release, str)):
        raise QSXError("base image source and release must be recorded")
    return Config(
        data["name"],
        source,
        state,
        disk,
        disk_gib,
        profile,
        git.get("branch_prefix", "agent/mathresearch"),
        guest_workspace,
        base_path,
        base_hash,
        base_source,
        base_release,
    )


def validate_task(task_id: str) -> str:
    if not SAFE_TASK.fullmatch(task_id):
        raise QSXError("task ID must contain only lowercase letters, digits, dot, dash, underscore")
    return task_id


def validate_checkpoint_id(checkpoint_id: str) -> str:
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}", checkpoint_id):
        raise QSXError("malformed checkpoint ID")
    return checkpoint_id
