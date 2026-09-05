from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from qsx.backend import LibvirtBackend, domain_xml
from qsx.config import Config
from qsx.disk import Checkpoints, create_disk
from qsx.errors import QSXError
from qsx.workspace import export_project, import_project


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()


def digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        h.update(str(path.relative_to(root)).encode())
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    source = tmp_path / "source"
    source.mkdir()
    return Config("MathResearch", source, tmp_path / "state", tmp_path / "disk.qcow2")


@pytest.mark.parametrize("kind", ["plain", "unborn", "clean", "dirty"])
def test_real_git_import_preserves_source_and_resumes(cfg: Config, kind: str) -> None:
    (cfg.source / "a.txt").write_text("initial")
    if kind != "plain":
        git(cfg.source, "init")
        git(cfg.source, "config", "user.name", "Test")
        git(cfg.source, "config", "user.email", "test@example.invalid")
    if kind in {"clean", "dirty"}:
        git(cfg.source, "add", "a.txt")
        git(cfg.source, "commit", "-m", "source")
    if kind == "dirty":
        (cfg.source / "a.txt").write_text("modified")
        (cfg.source / "new file.txt").write_text("new")
        (cfg.source / ".gitignore").write_text("*.cache\n")
        (cfg.source / "included.cache").write_text("ignored")
    before = digest(cfg.source)
    first = import_project(cfg, "task-1")
    second = import_project(cfg, "task-1")
    assert before == digest(cfg.source) and first["run_id"] == second["run_id"]
    workspace = cfg.state_dir / "tasks/task-1/workspace"
    assert git(workspace, "branch", "--show-current") == "agent/mathresearch/task-1"
    assert (workspace / "a.txt").read_text() == (cfg.source / "a.txt").read_text()
    if kind == "dirty":
        assert (workspace / "included.cache").exists()


def test_unsafe_symlink_and_export_collision(cfg: Config) -> None:
    (cfg.source / "bad").symlink_to("/etc/passwd")
    with pytest.raises(QSXError, match="escapes"):
        import_project(cfg, "task")
    with pytest.raises(QSXError):
        export_project(cfg, "task", cfg.source / "out")


def test_export_is_new_reviewable_copy(cfg: Config, tmp_path: Path) -> None:
    (cfg.source / "proof.txt").write_text("x")
    import_project(cfg, "task")
    workspace = cfg.state_dir / "tasks/task/workspace"
    (workspace / "proof.txt").write_text("result")
    git(workspace, "add", "proof.txt")
    git(workspace, "-c", "user.name=Agent", "-c", "user.email=a@invalid", "commit", "-m", "result")
    destination = tmp_path / "export"
    export_project(cfg, "task", destination)
    assert (destination / ".git").is_dir() and (destination / ".qsx-export/task.json").is_file()
    with pytest.raises(QSXError):
        export_project(cfg, "task", destination)


def test_real_offline_qcow_checkpoint_and_safe_rollback(cfg: Config) -> None:
    (cfg.source / "x").write_text("x")
    import_project(cfg, "task")
    create_disk(cfg.disk, 1)
    checkpoints = Checkpoints(cfg, lambda _: False)
    metadata = checkpoints.create("task", "PASS")
    assert metadata["dirty"] is False and metadata["validation_status"] == "PASS"
    original = cfg.disk.read_bytes()
    cfg.disk.write_bytes(b"damaged")
    checkpoints.rollback(str(metadata["checkpoint_id"]))
    assert cfg.disk.read_bytes() == original and list((cfg.state_dir / "rescue").iterdir())


def test_dirty_checkpoint_and_active_refusal(cfg: Config) -> None:
    (cfg.source / "x").write_text("x")
    import_project(cfg, "task")
    create_disk(cfg.disk, 1)
    (cfg.state_dir / "tasks/task/workspace/x").write_text("dirty")
    assert Checkpoints(cfg, lambda _: False).create("task")["dirty"] is True
    with pytest.raises(QSXError, match="SHUTOFF"):
        Checkpoints(cfg, lambda _: True).create("task")


def test_backend_contract_and_no_fallback(tmp_path: Path) -> None:
    xml = domain_xml("MathResearch", (tmp_path / "disk").resolve())
    assert "type='kvm'" in xml and "filesystem" not in xml and "interface" not in xml
    with pytest.raises(QSXError, match="KVM unavailable"):
        LibvirtBackend("test:///default").start("MathResearch")
