from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from qsx.backend import LibvirtBackend, domain_xml
from qsx.cli import main
from qsx.config import Config
from qsx.config import load as load_config
from qsx.disk import Checkpoints, create_disk
from qsx.errors import QSXError
from qsx.provision import GuestProvisioner
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


def inspect_staging(config: Config, task_id: str) -> tuple[str, str, bool, str | None]:
    workspace = config.state_dir / "tasks" / task_id / "workspace"
    branch = git(workspace, "branch", "--show-current")
    commit = git(workspace, "rev-parse", "HEAD")
    status = git(workspace, "status", "--porcelain=v1")
    return (
        branch,
        commit,
        bool(status),
        hashlib.sha256(status.encode()).hexdigest() if status else None,
    )


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
    checkpoints = Checkpoints(cfg, lambda _: False, inspect_staging)
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
    assert Checkpoints(cfg, lambda _: False, inspect_staging).create("task")["dirty"] is True
    with pytest.raises(QSXError, match="SHUTOFF"):
        Checkpoints(cfg, lambda _: True, inspect_staging).create("task")


def test_backend_contract_and_no_fallback(tmp_path: Path) -> None:
    xml = domain_xml("MathResearch", (tmp_path / "disk").resolve())
    assert 'type="kvm"' in xml and "filesystem" not in xml and "interface" not in xml
    with pytest.raises(QSXError, match="KVM unavailable"):
        LibvirtBackend("test:///default").start("MathResearch")


def test_config_refuses_disk_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    config = tmp_path / "qsx.toml"
    config.write_text(
        f'''schema_version=1\nname="MathResearch"\nsource="{source}"\n'''
        f'''state_dir="{tmp_path / "state"}"\ndisk="{source / "vm.qcow2"}"\n'''
    )
    with pytest.raises(QSXError, match="boundaries"):
        load_config(config)
    assert main(["--config", str(config), "create", "task"]) == 2
    assert not (source / "vm.qcow2").exists()


@pytest.mark.parametrize("checkpoint_id", ["../escape", "bad", "20200101T000000Z-../../bad"])
def test_rollback_rejects_malformed_checkpoint_id(cfg: Config, checkpoint_id: str) -> None:
    with pytest.raises(QSXError, match="malformed"):
        Checkpoints(cfg, lambda _: False, inspect_staging).rollback(checkpoint_id)


def test_rollback_rejects_incomplete_and_modified_checkpoint(cfg: Config) -> None:
    (cfg.source / "x").write_text("x")
    import_project(cfg, "task")
    create_disk(cfg.disk, 1)
    checkpoints = Checkpoints(cfg, lambda _: False, inspect_staging)
    checkpoint_id = str(checkpoints.create("task")["checkpoint_id"])
    image = cfg.state_dir / "checkpoints" / checkpoint_id / "disk.qcow2"
    image.write_bytes(image.read_bytes() + b"tampered")
    with pytest.raises(QSXError, match="hash mismatch"):
        checkpoints.rollback(checkpoint_id)
    image.unlink()
    with pytest.raises(QSXError, match="incomplete"):
        checkpoints.rollback(checkpoint_id)


def test_two_rollbacks_make_unique_rescues(cfg: Config) -> None:
    (cfg.source / "x").write_text("x")
    import_project(cfg, "task")
    create_disk(cfg.disk, 1)
    checkpoints = Checkpoints(cfg, lambda _: False, inspect_staging)
    checkpoint_id = str(checkpoints.create("task")["checkpoint_id"])
    checkpoints.rollback(checkpoint_id)
    checkpoints.rollback(checkpoint_id)
    assert len(list((cfg.state_dir / "rescue").glob("*.qcow2"))) == 2


def test_real_offline_guest_provisioning(cfg: Config, tmp_path: Path) -> None:
    (cfg.source / "proof.txt").write_text("input")
    import_project(cfg, "task")
    base = tmp_path / "base.qcow2"
    create_disk(base, 1)
    configured = Config(
        cfg.name,
        cfg.source,
        cfg.state_dir,
        cfg.disk,
        base_image=base,
        base_image_sha256=hashlib.sha256(base.read_bytes()).hexdigest(),
        base_image_source="https://cloud-images.ubuntu.com/releases/24.04/release/",
        base_image_release="synthetic test fixture, not a bootable guest",
    )
    result = GuestProvisioner(configured).provision("task")
    assert Path(result["disk"]).is_file() and Path(result["import_medium"]).is_file()
    subprocess.run(["qemu-img", "check", "-f", "qcow2", result["disk"]], check=True)
    listing = subprocess.run(
        ["xorriso", "-indev", result["import_medium"], "-find", "/", "-type", "f", "-exec", "lsdl"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert all(name in listing for name in ("workspace.tar", "user-data", "qsx-import.sh"))


def test_domain_xml_import_medium_is_read_only(tmp_path: Path) -> None:
    xml = domain_xml(
        "MathResearch", (tmp_path / "disk").resolve(), (tmp_path / "import.iso").resolve()
    )
    assert 'device="cdrom"' in xml and "<readonly" in xml and "<interface" not in xml


def test_libvirt_lifecycle_contract_and_collision(tmp_path: Path) -> None:
    class RecordingBackend(LibvirtBackend):
        def __init__(self) -> None:
            super().__init__("test:///default")
            self.calls: list[tuple[str, ...]] = []

        def _virsh(self, *args: str) -> str:
            self.calls.append(args)
            if args == ("list", "--all", "--name"):
                return ""
            if args == ("domstate", "MathResearch"):
                return "shut off"
            return ""

    xml = tmp_path / "domain.xml"
    xml.write_text(domain_xml("MathResearch", (tmp_path / "disk.qcow2").resolve()))
    backend = RecordingBackend()
    backend.define(xml)
    assert backend.status("MathResearch") == "shut off"
    backend.shutdown("MathResearch", timeout=1)
    assert ("define", str(xml)) in backend.calls and ("shutdown", "MathResearch") in backend.calls

    class ExistingBackend(RecordingBackend):
        def _virsh(self, *args: str) -> str:
            if args == ("list", "--all", "--name"):
                return "MathResearch"
            return super()._virsh(*args)

    with pytest.raises(QSXError, match="already exists"):
        ExistingBackend().define(xml)
