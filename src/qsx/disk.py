from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .config import Config, validate_checkpoint_id, validate_task
from .errors import QSXError
from .memory import ResearchMemory
from .util import atomic_json, run, sha256


def create_disk(path: Path, gib: int = 60) -> None:
    if path.exists():
        raise QSXError("refusing to overwrite disk")
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["qemu-img", "create", "-f", "qcow2", str(path), f"{gib}G"])


class WorkspaceInspector(Protocol):
    def __call__(self, config: Config, task_id: str) -> tuple[str, str, bool, str | None, str]: ...


class Checkpoints:
    def __init__(
        self,
        config: Config,
        is_active: Callable[[str], bool],
        inspect_workspace: WorkspaceInspector,
    ) -> None:
        self.config, self.is_active, self.inspect_workspace = config, is_active, inspect_workspace

    def create(self, task_id: str, validation: str = "NOT_RUN") -> dict[str, object]:
        validate_task(task_id)
        if self.is_active(self.config.name):
            raise QSXError("cold checkpoint requires a SHUTOFF domain")
        branch, commit, dirty, dirty_hash, workspace_hash = self.inspect_workspace(
            self.config, task_id
        )
        checkpoint_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        directory = self.config.state_dir / "checkpoints" / checkpoint_id
        directory.mkdir(parents=True, mode=0o700)
        target = directory / "disk.qcow2"
        shutil.copy2(self.config.disk, target)
        run(["qemu-img", "check", "-f", "qcow2", str(target)])
        pointer_path = self.config.state_dir / "checkpoint-state.json"
        pointer = json.loads(pointer_path.read_text()) if pointer_path.exists() else {}
        memory = ResearchMemory(self.config)
        journal = memory.verify()
        task_metadata = json.loads(
            (self.config.state_dir / "tasks" / task_id / "task.json").read_text()
        )
        lineage_id = str(
            pointer.get("lineage_id") or task_metadata.get("lineage_id") or uuid.uuid4()
        )
        metadata: dict[str, object] = {
            "schema_version": 1,
            "checkpoint_id": checkpoint_id,
            "environment": self.config.name,
            "task_id": task_id,
            "project_id": memory.project_id,
            "environment_id": memory.environment_id,
            "lineage_id": lineage_id,
            "branch": branch,
            "git_commit": commit,
            "dirty": dirty,
            "dirty_manifest_sha256": dirty_hash,
            "workspace_sha256": workspace_hash,
            "timestamp": datetime.now(UTC).isoformat(),
            "parent_checkpoint": pointer.get("current_checkpoint"),
            "disk_image": str(target),
            "disk_sha256": sha256(target),
            "validation_status": validation,
            "consistency": "cold",
            "observed_by": "controller",
            "journal_revision_before_checkpoint": journal["head_sha256"],
            "journal_sequence_before_checkpoint": journal["sequence"],
            "evidence_refs": [],
        }
        atomic_json(directory / "manifest.json", metadata)
        atomic_json(
            pointer_path,
            {
                "current_checkpoint": checkpoint_id,
                "lineage_id": lineage_id,
                "branch": branch,
                "git_commit": commit,
                "workspace_state_ref": f"workspace-manifest-v1:sha256:{workspace_hash}",
            },
        )
        memory.append(
            kind="lifecycle",
            task_id=task_id,
            summary="Cold checkpoint created from stopped guest disk",
            status="PASS",
            origin="controller",
            evidence="controller_observed",
            git_commit=commit,
            checkpoint_id=checkpoint_id,
            operation_id=f"checkpoint:{checkpoint_id}",
        )
        return metadata

    def rollback(
        self,
        checkpoint_id: str,
        preserve_current: bool = True,
        operation_id: str | None = None,
    ) -> None:
        if self.is_active(self.config.name):
            raise QSXError("rollback requires a SHUTOFF domain")
        validate_checkpoint_id(checkpoint_id)
        checkpoint_root = (self.config.state_dir / "checkpoints").resolve()
        directory = (checkpoint_root / checkpoint_id).resolve()
        if not directory.is_relative_to(checkpoint_root):
            raise QSXError("checkpoint escapes checkpoint storage")
        source = directory / "disk.qcow2"
        manifest_path = directory / "manifest.json"
        if not source.is_file() or not manifest_path.is_file():
            raise QSXError("checkpoint is incomplete")
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise QSXError("checkpoint manifest is invalid") from exc
        if manifest.get("checkpoint_id") != checkpoint_id:
            raise QSXError("checkpoint manifest ID mismatch")
        if manifest.get("disk_sha256") != sha256(source):
            raise QSXError("checkpoint disk hash mismatch")
        run(["qemu-img", "check", "-f", "qcow2", str(source)])
        memory = ResearchMemory(self.config)
        operation_id = operation_id or f"rollback:{uuid.uuid4()}"
        operation_task = str(manifest["task_id"])
        operation_parameters = {
            "checkpoint_id": checkpoint_id,
            "preserve_current": preserve_current,
            "disk": str(self.config.disk),
        }
        prior = memory.begin_operation(
            operation_id, operation_task, "rollback", operation_parameters
        )
        if prior == "COMPLETED":
            return
        replaced = False
        try:
            if preserve_current:
                rescue = (
                    self.config.state_dir
                    / "rescue"
                    / (f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}.qcow2")
                )
                rescue.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(self.config.disk, rescue)
            temporary = self.config.disk.with_suffix(".rollback.tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, self.config.disk)
            replaced = True
            lineage_id = str(uuid.uuid4())
            atomic_json(
                self.config.state_dir / "checkpoint-state.json",
                {
                    "current_checkpoint": checkpoint_id,
                    "lineage_id": lineage_id,
                    "branch": manifest["branch"],
                    "git_commit": manifest["git_commit"],
                    "workspace_state_ref": f"workspace-manifest-v1:sha256:{manifest['workspace_sha256']}",
                },
            )
            memory.append(
                kind="lifecycle",
                task_id=str(manifest["task_id"]),
                summary="Guest disk rolled back; controller memory was retained",
                status="PASS",
                origin="controller",
                evidence="controller_observed",
                git_commit=str(manifest["git_commit"]),
                checkpoint_id=checkpoint_id,
                operation_id=operation_id,
            )
            memory.finish_operation(
                operation_id, "COMPLETED", task_id=operation_task, kind="rollback"
            )
        except BaseException:
            memory.finish_operation(
                operation_id,
                "RECONCILE" if replaced else "FAILED",
                task_id=operation_task,
                kind="rollback",
            )
            raise
