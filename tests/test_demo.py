"""Demo command contracts only: no hypervisor, host changes or model login."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from qsx_alpine.cli import parser as cli_parser

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/demo_debian.py"
SPEC = importlib.util.spec_from_file_location("debian_demo_contract", SCRIPT)
assert SPEC and SPEC.loader
demo: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


def test_default_plan_never_executes_or_creates_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("read-only demo plan must not execute any command")

    monkeypatch.setattr(demo.subprocess, "run", forbidden)
    work, cache = tmp_path / "future-work", tmp_path / "future-cache"
    assert demo.main(["--work-dir", str(work), "--cache", str(cache)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "PLAN_ONLY_NO_EXECUTION"
    assert result["model_task"] == "NOT_RUN"
    assert not work.exists() and not cache.exists()
    assert {command["argv"][1] for command in result["commands"]} >= {
        "template-new", "template-lock", "image-build", "image-verify", "instance-create",
        "start", "finalize-import", "run", "network-cut", "stop",
    }


def test_plan_commands_parse_with_current_public_cli(tmp_path: Path) -> None:
    args = demo.parser().parse_args(["--cache", str(tmp_path / "cache"),
                                    "--work-dir", str(tmp_path / "work")])
    for command in demo.command_plan(args):
        argv = command["argv"][1:]
        if argv[:1] == ["run"]:
            # The public CLI separates guest argv at -- before argparse runs.
            boundary = argv.index("--")
            assert argv[boundary + 1:]
            argv = argv[:boundary]
        parsed = cli_parser().parse_args(argv)
        assert parsed.command
    builds = [command["argv"] for command in demo.command_plan(args)
              if command["argv"][1] == "image-build"]
    assert builds[0] != builds[1]
    assert all(argv[-2:] == ["--accelerator", "kvm"] for argv in builds)


def test_run_rejects_existing_or_repo_overlapping_workdir(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    args = demo.parser().parse_args(["--run", "--work-dir", str(existing),
                                    "--cache", str(tmp_path / "cache")])
    with pytest.raises(demo.DemoError, match="fresh"):
        demo._fresh_directory(args)
    args.work_dir = SCRIPT.parent / "unsafe-generated"
    with pytest.raises(demo.DemoError, match="outside"):
        demo._fresh_directory(args)
    assert not args.work_dir.exists()


def test_synthetic_executable_contract_records_exit_and_failure_without_vm_claim(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "fake-qsx-contract-only"
    executable.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        "if sys.argv[1] == 'good':\n"
        " print(json.dumps({'evidence': 'SYNTHETIC_COMMAND_CONTRACT_NOT_VM', 'returncode': 0}))\n"
        "else:\n print('synthetic CLI failure', file=sys.stderr)\n sys.exit(7)\n"
    )
    executable.chmod(0o755)
    report = tmp_path / "contract-not-vm.json"
    steps = demo.Steps(str(executable), report)
    assert steps.run("synthetic success", ["good"])["evidence"] == (
        "SYNTHETIC_COMMAND_CONTRACT_NOT_VM"
    )
    assert steps.run("synthetic retry", ["bad"], retry=True) == {"retry": True}
    with pytest.raises(demo.DemoError, match="exit 7"):
        steps.run("synthetic failure", ["bad"])
    entries = json.loads(report.read_text())
    assert [entry["status"] for entry in entries["steps"]] == ["PASS", "RETRY", "FAIL"]
    assert entries["model_task"] == "NOT_RUN"
    assert entries["hardware_isolation_audit"] == "NOT_RUN"
    assert entries["status"] == "RUNNING"


def test_false_semantic_assertion_never_passes(tmp_path: Path) -> None:
    steps = demo.Steps("unused", tmp_path / "contract-not-vm.json")
    with pytest.raises(demo.DemoError, match="readiness"):
        steps.require("readiness", False, "required marker absent")
    assert steps.data["steps"][-1]["status"] == "FAIL"


def test_continue_rejects_unowned_state_without_creating_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    work = tmp_path / "not-prepared"
    assert demo.main(["--continue", "--work-dir", str(work), "--qsx", sys.executable]) == 2
    assert not work.exists()
    assert "demo_debian:" in capsys.readouterr().err


@pytest.mark.parametrize("partially_running", [False, True])
def test_synthetic_prepare_then_continue_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], partially_running: bool,
) -> None:
    """Fake installed executable exercises orchestration, never images or a VM."""
    executable = tmp_path / "synthetic-qsx-contract-only"
    executable.write_text(f"#!{sys.executable}\n" + r'''
import hashlib
import json
import sys
from pathlib import Path

argv = sys.argv[1:]
command = argv[0]
def option(name):
    return argv[argv.index(name) + 1]
def emit(value):
    print(json.dumps({"test_scope": "SYNTHETIC_COMMAND_CONTRACT_NOT_A_VM", **value}))
def save(path, value):
    path.write_text(json.dumps(value))
if command == "doctor":
    emit({"tools": {name: {"status": "PASS", "test_fixture": True} for name in
          ["qemu-img", "qemu-system-x86_64", "xorriso", "virsh", "gpg", "cosign"]},
          "kvm_access_hint": True})
elif command in ["template-check", "template-new"]:
    emit({"synthetic_recipe": True})
elif command == "template-lock":
    emit({"lock": str(Path(option("--cache")) / (Path(argv[1]).stem + ".json"))})
elif command == "image-build":
    output = Path(option("--output"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"synthetic command contract bytes; not QCOW2")
    emit({"image_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
          "cache_hit": "cached" in output.name})
elif command == "image-verify":
    emit({"synthetic_contract_only": True})
elif command == "instance-create":
    root = Path(option("--state-root")) / argv[1]
    root.mkdir(parents=True)
    path = root / "instance.json"
    save(path, {"name": argv[1], "state": "not defined", "import_finalized": False,
                "readiness_calls": 0, "task_calls": 0, "starts": 0})
    emit({"instance_manifest": str(path)})
else:
    path = Path(argv[1])
    state = json.loads(path.read_text())
    if command == "status":
        emit(state)
    elif command == "start":
        if state["state"] == "running":
            print("already running; duplicate start is an orchestration bug", file=sys.stderr)
            sys.exit(8)
        state["state"] = "running"
        state["starts"] += 1
        save(path, state)
        emit(state)
    elif command == "finalize-import":
        assert state["state"] == "running"
        state.update(state="shut off", import_finalized=True)
        save(path, state)
        emit(state)
    elif command == "stop":
        state["state"] = "shut off"
        save(path, state)
        emit(state)
    elif command == "network-cut":
        emit({"network_profile": "offline"})
    elif command == "run":
        guest = argv[argv.index("--") + 1:]
        if guest == ["true"]:
            state["readiness_calls"] += 1
            save(path, state)
            if state["readiness_calls"] == 1:
                print("synthetic shell not ready yet", file=sys.stderr)
                sys.exit(2)
            emit({"stdout": "", "returncode": 0})
        elif guest[-1:] == ["/workspace/project/check.py"]:
            assert state["readiness_calls"] >= 2
            state["task_calls"] += 1
            save(path, state)
            emit({"stdout": "QSX_TASK_PASS_NONROOT", "returncode": 0})
        elif guest[:1] == ["test"]:
            assert not (path.parent / "only-a.txt").exists()
            emit({"stdout": "", "returncode": 0})
        elif "only-a.txt" in guest[-1]:
            (path.parent / "only-a.txt").write_text("private-a")
            emit({"stdout": "", "returncode": 0})
        else:
            assert "QSX_SERIAL_AFTER_CUT" in guest[-1]
            emit({"stdout": "QSX_SERIAL_AFTER_CUT", "returncode": 0})
    else:
        raise AssertionError(command)
''')
    executable.chmod(0o755)
    monkeypatch.setattr(demo.time, "sleep", lambda seconds: None)
    work, cache = tmp_path / "run", tmp_path / "cache"
    assert demo.main(["--run", "--prepare-only", "--work-dir", str(work),
                      "--cache", str(cache), "--qsx", str(executable)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "AWAITING_HOST_STORAGE_ACCESS"
    state = json.loads((work / "demo-state.json").read_text())
    report_path = work / "demo-report.json"
    report = json.loads(report_path.read_text())
    report["test_scope"] = "SYNTHETIC_COMMAND_CONTRACT_NOT_A_VM"
    report_path.write_text(json.dumps(report))
    first = Path(state["instances"][0])
    if partially_running:
        current = json.loads(first.read_text())
        current.update(state="running", import_finalized=True)
        first.write_text(json.dumps(current))
    assert demo.main(["--continue", "--work-dir", str(work), "--qsx", str(executable)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS_LOCAL_DEMO_WITH_EXPLICIT_EXCLUSIONS"
    for instance in state["instances"]:
        current = json.loads(Path(instance).read_text())
        assert current["state"] == "shut off" and current["import_finalized"]
        assert current["task_calls"] == 1 and current["readiness_calls"] == 2
    first_state = json.loads(first.read_text())
    assert first_state["starts"] == (0 if partially_running else 2)
    report = json.loads(report_path.read_text())
    assert report["test_scope"] == "SYNTHETIC_COMMAND_CONTRACT_NOT_A_VM"
    assert report["operator_login"] == "AWAITING_OPERATOR_LOGIN"
    assert report["model_task"] == "NOT_RUN"
