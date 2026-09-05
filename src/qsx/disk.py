from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .config import Config, validate_task
from .errors import QSXError
from .util import atomic_json, run, sha256


def create_disk(path: Path, gib: int = 60) -> None:
    if path.exists():
        raise QSXError("refusing to overwrite disk")
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["qemu-img", "create", "-f", "qcow2", str(path), f"{gib}G"])


class Checkpoints:
    def __init__(self, config: Config, is_active: Callable[[str], bool]) -> None:
        self.config, self.is_active = config, is_active

    def create(self, task_id: str, validation: str = "NOT_RUN") -> dict[str, object]:
        validate_task(task_id)
        if self.is_active(self.config.name):
            raise QSXError("cold checkpoint requires a SHUTOFF domain")
        task = json.loads((self.config.state_dir / "tasks" / task_id / "task.json").read_text())
        workspace = self.config.state_dir / "tasks" / task_id / "workspace"
        status = run(["git", "status", "--porcelain=v1"], workspace)
        checkpoint_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        directory = self.config.state_dir / "checkpoints" / checkpoint_id
        directory.mkdir(parents=True, mode=0o700)
        target = directory / "disk.qcow2"
        shutil.copy2(self.config.disk, target)
        run(["qemu-img", "check", "-f", "qcow2", str(target)])
        previous = sorted((self.config.state_dir / "checkpoints").glob("*/manifest.json"))
        metadata: dict[str, object] = {
            "schema_version": 1,
            "checkpoint_id": checkpoint_id,
            "environment": self.config.name,
            "task_id": task_id,
            "branch": task["branch"],
            "git_commit": run(["git", "rev-parse", "HEAD"], workspace),
            "dirty": bool(status),
            "dirty_manifest_sha256": __import__("hashlib").sha256(status.encode()).hexdigest()
            if status
            else None,
            "timestamp": datetime.now(UTC).isoformat(),
            "parent_checkpoint": previous[-1].parent.name if previous else None,
            "disk_image": str(target),
            "disk_sha256": sha256(target),
            "validation_status": validation,
            "consistency": "cold",
            "observed_by": "controller",
        }
        atomic_json(directory / "manifest.json", metadata)
        return metadata

    def rollback(self, checkpoint_id: str, preserve_current: bool = True) -> None:
        if self.is_active(self.config.name):
            raise QSXError("rollback requires a SHUTOFF domain")
        source = self.config.state_dir / "checkpoints" / checkpoint_id / "disk.qcow2"
        if not source.is_file():
            raise QSXError("checkpoint does not exist")
        if preserve_current:
            rescue = self.config.state_dir / "rescue" / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.qcow2"
            rescue.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(self.config.disk, rescue)
        temporary = self.config.disk.with_suffix(".rollback.tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, self.config.disk)
