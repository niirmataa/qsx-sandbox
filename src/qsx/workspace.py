from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .config import Config, validate_task
from .errors import QSXError
from .util import atomic_json, run

EXCLUDED_NAMES = {".git", ".ssh", ".gnupg", ".aws", ".codex", "id_rsa", "id_ed25519"}


def _manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            if (path.parent / target).resolve().is_relative_to(root.resolve()):
                result[str(relative)] = "symlink:" + target
            else:
                raise QSXError(f"symlink escapes source: {relative}")
        elif stat.S_ISREG(info.st_mode):
            result[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif stat.S_ISDIR(info.st_mode):
            result[str(relative) + "/"] = "directory"
        else:
            raise QSXError(f"unsupported filesystem object: {relative}")
    return result


def _git(repo: Path, *args: str) -> str:
    return run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "credential.helper=",
            "-c",
            "filter.lfs.smudge=",
            "-c",
            "filter.lfs.required=false",
            *args,
        ],
        repo,
    )


def _validate_git(source: Path) -> bool:
    dotgit = source / ".git"
    if not dotgit.exists():
        return False
    if not dotgit.is_dir():
        raise QSXError("external gitdir is not supported")
    forbidden = ["modules", "shallow", "info/sparse-checkout"]
    if any((dotgit / item).exists() for item in forbidden):
        raise QSXError("submodules, shallow clones and sparse checkouts are not supported")
    if (source / ".gitattributes").exists() and "filter=lfs" in (
        source / ".gitattributes"
    ).read_text(errors="replace"):
        raise QSXError("Git LFS is not supported")
    return True


def import_project(config: Config, task_id: str) -> dict[str, object]:
    validate_task(task_id)
    source = config.source
    if not source.is_dir() or source.is_symlink():
        raise QSXError("source must be a real directory")
    task_root = config.state_dir / "tasks" / task_id
    metadata_path = task_root / "task.json"
    if metadata_path.exists():
        existing = cast(dict[str, object], json.loads(metadata_path.read_text()))
        return existing
    if task_root.exists():
        raise QSXError("incomplete task exists; inspect it before retrying")
    before = _manifest(source)
    has_git = _validate_git(source)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=config.state_dir))
    try:
        workspace = staging / "workspace"
        source_commit = None
        if has_git:
            try:
                source_commit = _git(source, "rev-parse", "HEAD")
            except QSXError:
                source_commit = None
        if source_commit:
            bundle = staging / "history.bundle"
            _git(source, "bundle", "create", str(bundle), "--all")
            run(["git", "clone", "--no-hardlinks", str(bundle), str(workspace)])
            # Remove all tracked files before overlaying the exact current filesystem state.
            for child in workspace.iterdir():
                if child.name != ".git":
                    shutil.rmtree(
                        child
                    ) if child.is_dir() and not child.is_symlink() else child.unlink()
        else:
            workspace.mkdir()
            _git(workspace, "init", "--initial-branch=qsx-baseline")
        for item in source.iterdir():
            if item.name in EXCLUDED_NAMES:
                continue
            target = workspace / item.name
            if item.is_dir() and not item.is_symlink():
                shutil.copytree(item, target, symlinks=True)
            else:
                shutil.copy2(item, target, follow_symlinks=False)
        after = _manifest(source)
        if before != after:
            raise QSXError("source changed during import; retry while writers are stopped")
        _git(workspace, "add", "-A")
        _git(
            workspace,
            "-c",
            "user.name=QSX Import",
            "-c",
            "user.email=qsx-import@local.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "QSX technical import baseline",
        )
        baseline = _git(workspace, "rev-parse", "HEAD")
        branch = f"{config.branch_prefix}/{task_id}"
        _git(workspace, "switch", "-c", branch)
        manifest_bytes = json.dumps(before, sort_keys=True).encode()
        data: dict[str, object] = {
            "schema_version": 1,
            "environment": config.name,
            "task_id": task_id,
            "run_id": str(uuid.uuid4()),
            "branch": branch,
            "source_commit": source_commit,
            "baseline_commit": baseline,
            "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "created_at": datetime.now(UTC).isoformat(),
            "rules_version": 1,
        }
        atomic_json(staging / "task.json", data)
        task_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, task_root)
        return data
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def resume_task(config: Config, task_id: str) -> dict[str, object]:
    """Return controller truth without mutating the obsolete import staging checkout."""
    validate_task(task_id)
    metadata = config.state_dir / "tasks" / task_id / "task.json"
    provision = config.state_dir / "tasks" / task_id / "provision.json"
    if not metadata.is_file() or not provision.is_file():
        raise QSXError("task is not completely provisioned")
    return cast(dict[str, object], json.loads(metadata.read_text()))


def export_project(
    config: Config, task_id: str, destination: Path, workspace: Path | None = None
) -> None:
    validate_task(task_id)
    if destination.exists() or destination.resolve().is_relative_to(config.source):
        raise QSXError("export destination must be new and outside the source")
    root = config.state_dir / "tasks" / task_id
    data = json.loads((root / "task.json").read_text())
    workspace = workspace or root / "workspace"
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copytree(
            workspace,
            temporary,
            symlinks=True,
            ignore=shutil.ignore_patterns(".ssh", ".gnupg", ".aws", ".codex"),
        )
        (temporary / ".qsx-export").mkdir(mode=0o700)
        atomic_json(temporary / ".qsx-export/task.json", data)
        diff = _git(workspace, "diff", f"{data['baseline_commit']}..{data['branch']}")
        (temporary / ".qsx-export/baseline.diff").write_text(diff)
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
