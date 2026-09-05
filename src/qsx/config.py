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
    if state == source or state.is_relative_to(source) or source.is_relative_to(state):
        raise QSXError("source and private state must not overlap")
    profile = data.get("network_profile", "offline")
    if profile not in {"offline", "openai"}:
        raise QSXError("network_profile must be offline or openai")
    git = data.get("git", {})
    if git.get("source_read_only", True) is not True or git.get("allow_push", False):
        raise QSXError("source_read_only=true and allow_push=false are mandatory")
    disk_gib = data.get("disk_gib", 60)
    if disk_gib != 60:
        raise QSXError("MathResearch MVP requires a 60 GiB private disk")
    return Config(
        data["name"],
        source,
        state,
        disk,
        disk_gib,
        profile,
        git.get("branch_prefix", "agent/mathresearch"),
    )


def validate_task(task_id: str) -> str:
    if not SAFE_TASK.fullmatch(task_id):
        raise QSXError("task ID must contain only lowercase letters, digits, dot, dash, underscore")
    return task_id
