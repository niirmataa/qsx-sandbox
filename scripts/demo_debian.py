#!/usr/bin/env python3
"""Local Debian acceptance through the installed qsx-alpine CLI only.

Default mode prints a plan. This script never installs host packages, changes ACLs,
logs into a model provider, or labels a command-contract simulation as a VM test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MARKER = "qsx-alpine-debian-demo-v1"


class DemoError(RuntimeError):
    pass


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="print commands; no writes or execution")
    mode.add_argument("--run", action="store_true", help="execute on the operator's Debian host")
    mode.add_argument("--continue", dest="continue_run", action="store_true",
                      help="continue a prepared demo after local storage access review")
    p.add_argument("--cache", type=Path)
    p.add_argument("--work-dir", type=Path)
    p.add_argument("--qsx", default="qsx-alpine", help="installed CLI executable")
    p.add_argument("--templates-dir", type=Path, default=ROOT / "templates")
    p.add_argument("--profiles-dir", type=Path, default=ROOT / "agent-profiles")
    p.add_argument("--prepare-only", action="store_true",
                   help="build/create, then stop for operator review of libvirt file access")
    p.add_argument("--interactive", action="store_true",
                   help="open local console and Python agent; operator exits each explicitly")
    p.add_argument("--boot-timeout", type=int, default=300)
    return p


def command_plan(args: argparse.Namespace) -> list[dict[str, Any]]:
    work = str(args.work_dir or "<fresh-work-dir>")
    cache = str(args.cache or "<cache-dir>")
    starter = str(args.templates_dir / "starter.toml")
    profile = str(args.profiles_dir / "python-shell.toml")
    commands = [
        ("preflight", ["doctor"]),
        ("starter recipe", ["template-check", starter]),
        ("new arbitrary recipe", ["template-new", work + "/task.toml", "--name", "demo-task",
                                  "--agent-profile", profile, "--package", "python3"]),
        ("starter inputs", ["template-lock", starter, "--cache", cache]),
        ("starter build", ["image-build", "<starter-lock>", "--cache", cache, "--output",
                           work + "/images/starter.qcow2", "--accelerator", "kvm"]),
        ("verified cache build", ["image-build", "<starter-lock>", "--cache", cache, "--output",
                                  work + "/images/starter-cached.qcow2", "--accelerator", "kvm"]),
        ("starter verify", ["image-verify", work + "/images/starter.qcow2.manifest.json"]),
        ("cached verify", ["image-verify", work + "/images/starter-cached.qcow2.manifest.json"]),
        ("task inputs", ["template-lock", work + "/task.toml", "--cache", cache]),
        ("task build", ["image-build", "<task-lock>", "--cache", cache, "--output",
                        work + "/images/task.qcow2", "--accelerator", "kvm"]),
        ("task verify", ["image-verify", work + "/images/task.qcow2.manifest.json"]),
    ]
    for name in ("demo-a", "demo-b"):
        instance = work + "/instances/" + name + "/instance.json"
        commands.extend([
            (name + " create", ["instance-create", name, "--image",
                                work + "/images/task.qcow2.manifest.json", "--source",
                                work + "/source", "--state-root", work + "/instances",
                                "--policy", work + "/operator-policy.toml"]),
            (name + " first start", ["start", instance]),
            (name + " finalize; readiness is polled", ["finalize-import", instance]),
            (name + " normal start", ["start", instance]),
            (name + " normal boot readiness; poll only a no-op", ["run", instance,
                                                                  "--timeout", "10", "--", "true"]),
            (name + " nonroot task", ["run", instance, "--", "python3",
                                      "/workspace/project/check.py"]),
            (name + " cut network", ["network-cut", instance]),
            (name + " serial remains available", ["run", instance, "--", "id", "-u"]),
            (name + " stop", ["stop", instance]),
        ])
    return [{"step": label, "argv": [args.qsx, *argv]} for label, argv in commands]


def _save(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class Steps:
    def __init__(self, executable: str, report: Path, prior: dict[str, Any] | None = None):
        self.executable, self.report = executable, report
        self.data = prior or {
            "schema": MARKER, "status": "RUNNING", "steps": [],
            "operator_login": "AWAITING_OPERATOR_LOGIN",
            "model_task": "NOT_RUN", "openai_egress": "NOT_IMPLEMENTED_FAIL_CLOSED",
            "checkpoint_rollback_context": "OUT_OF_SCOPE_STANDALONE_IMAGE_ITERATION",
            "interactive_console": "NOT_RUN", "interactive_agent": "NOT_RUN",
            "hardware_isolation_audit": "NOT_RUN",
        }
        self.data["status"] = "RUNNING"

    def record(self, entry: dict[str, Any]) -> None:
        self.data["steps"].append(entry)
        _save(self.report, self.data)

    def require(self, name: str, condition: bool, detail: str) -> None:
        self.record({"step": name, "status": "PASS" if condition else "FAIL", "detail": detail})
        if not condition:
            raise DemoError(f"{name}: {detail}")

    def run(self, name: str, argv: list[str], *, timeout: int = 3600,
            retry: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        try:
            result = subprocess.run([self.executable, *argv], capture_output=True, text=True,
                                    timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.record({"step": name, "argv": [self.executable, *argv], "status": "FAIL",
                         "error": str(exc)})
            raise DemoError(f"{name}: {exc}") from exc
        entry = {"step": name, "argv": [self.executable, *argv],
                 "status": "PASS" if result.returncode == 0 else ("RETRY" if retry else "FAIL"),
                 "returncode": result.returncode, "seconds": time.monotonic() - started,
                 "stdout": result.stdout, "stderr": result.stderr,
                 "evidence": "LOCAL_CLI_RESULT; semantic checks are recorded separately"}
        self.record(entry)
        if result.returncode:
            if retry:
                return {"retry": True}
            raise DemoError(f"{name}: CLI exit {result.returncode}; see {self.report}")
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise DemoError(f"{name}: expected JSON from installed CLI") from exc
        if not isinstance(payload, dict):
            raise DemoError(f"{name}: expected a JSON object")
        return payload


def _fresh_directory(args: argparse.Namespace) -> tuple[Path, Path]:
    if args.work_dir is None or args.cache is None:
        raise DemoError("--run requires explicit --work-dir and --cache")
    requested_work = args.work_dir.expanduser()
    if requested_work.is_symlink():
        raise DemoError("--run requires a fresh work-dir, not a symbolic link")
    work, cache = requested_work.resolve(), args.cache.expanduser().resolve()
    for path in (work, cache):
        if path == Path("/") or path.is_relative_to(ROOT) or ROOT.is_relative_to(path):
            raise DemoError("work/cache paths must be outside the source repository")
    if work == cache or work.is_relative_to(cache) or cache.is_relative_to(work):
        raise DemoError("work-dir and reusable cache must be separate directories")
    if work.exists() or work.is_symlink():
        raise DemoError("--run requires a fresh work-dir; use --continue only after --prepare-only")
    work.mkdir(parents=True, mode=0o700)
    return work, cache


def prepare(args: argparse.Namespace, steps: Steps, work: Path, cache: Path) -> dict[str, Any]:
    doctor = steps.run("host preflight", ["doctor"])
    steps.require("required tools", all(
        doctor.get("tools", {}).get(name, {}).get("status") == "PASS"
        for name in ("qemu-img", "qemu-system-x86_64", "xorriso", "virsh", "gpg", "cosign")
    ), "all build/runtime tools must be installed and executable")
    steps.require("KVM device", bool(doctor.get("kvm_access_hint")),
                  "operator needs access to /dev/kvm; no TCG fallback")
    source = work / "source"
    source.mkdir(mode=0o700)
    (source / "check.py").write_text(
        "import os\nassert os.getuid() != 0\nassert pow(7, 12, 13) == 1\n"
        "print('QSX_TASK_PASS_NONROOT')\n", encoding="utf-8",
    )
    policy = work / "operator-policy.toml"
    policy.write_text("schema_version = 1\nnetwork_profile = \"offline\"\n[limits]\n"
                      "memory_mib = 4096\nvcpus = 4\ndisk_gib = 16\n", encoding="utf-8")
    starter = args.templates_dir.resolve() / "starter.toml"
    steps.run("starter recipe", ["template-check", str(starter)])
    recipe = work / "task.toml"
    steps.run("new arbitrary template", ["template-new", str(recipe), "--name", "demo-task",
              "--agent-profile", str(args.profiles_dir.resolve() / "python-shell.toml"),
              "--package", "python3"])
    lock = steps.run("starter inputs", ["template-lock", str(starter), "--cache", str(cache)])
    starter_image = work / "images/starter.qcow2"
    build = steps.run("first starter build", ["image-build", str(lock["lock"]), "--cache",
                      str(cache), "--output", str(starter_image), "--accelerator", "kvm"])
    cached_image = work / "images/starter-cached.qcow2"
    cached = steps.run("verified cache build", ["image-build", str(lock["lock"]), "--cache",
                       str(cache), "--output", str(cached_image), "--accelerator", "kvm"])
    steps.require("cache reused", cached.get("cache_hit") is True,
                  "second build must reuse the verified retained image")
    steps.require("cached publication bytes", build.get("image_sha256") == cached.get("image_sha256"),
                  "cache publication equality; this is not proof of independent byte reproducibility")
    for name, image in (("starter", starter_image), ("cached", cached_image)):
        steps.run(name + " image integrity", ["image-verify", str(image) + ".manifest.json"])
    task_lock = steps.run("task inputs", ["template-lock", str(recipe), "--cache", str(cache)])
    task_image = work / "images/task.qcow2"
    steps.run("arbitrary task build", ["image-build", str(task_lock["lock"]), "--cache", str(cache),
              "--output", str(task_image), "--accelerator", "kvm"])
    task_manifest = str(task_image) + ".manifest.json"
    steps.run("task image integrity", ["image-verify", task_manifest])
    instances = []
    suffix = uuid.uuid4().hex[:8]
    for label in ("a", "b"):
        created = steps.run("private instance " + label, ["instance-create", "demo-" + label + "-" + suffix,
                            "--image", task_manifest, "--source", str(source), "--state-root",
                            str(work / "instances"), "--policy", str(policy), "--memory-mib", "1024",
                            "--vcpus", "1", "--disk-gib", "8"])
        instances.append(str(created["instance_manifest"]))
    state = {"schema": MARKER, "work_dir": str(work), "cache": str(cache),
             "instances": instances, "base_image": str(task_image),
             "base_sha256": _sha256(task_image), "source_sha256": _sha256(source / "check.py"),
             "phase": "PREPARED", "starter_lock": str(lock["lock"]),
             "task_lock": str(task_lock["lock"])}
    _save(work / "demo-state.json", state)
    return state


def execute_guests(args: argparse.Namespace, steps: Steps, state: dict[str, Any]) -> None:
    instances = state["instances"]
    for index, instance in enumerate(instances):
        label = "instance " + str(index + 1)
        started = time.monotonic()
        initial = steps.run(label + " current state", ["status", instance])
        if not initial.get("import_finalized"):
            if initial.get("state") != "running":
                steps.require(label + " first start permitted", initial.get("state") in {
                    "not defined", "shut off",
                }, "unexpected paused/crashed domain needs explicit local review")
                steps.run(label + " first start", ["start", instance])
            deadline = time.monotonic() + args.boot_timeout
            while True:
                response = steps.run(label + " import readiness/finalize", ["finalize-import", instance],
                                     timeout=args.boot_timeout + 120, retry=True)
                if not response.get("retry"):
                    break
                if time.monotonic() >= deadline:
                    raise DemoError(label + ": import readiness timeout; inspect local boot evidence")
                time.sleep(2)
        status = steps.run(label + " finalized state", ["status", instance])
        steps.require(label + " import finalized", status.get("import_finalized") is True,
                      "runtime must observe completed import and detached read-only medium")
        steps.record({"step": label + " ensure finalized instance duration", "status": "PASS",
                      "seconds": time.monotonic() - started})
        if status.get("state") != "running":
            steps.require(label + " normal start permitted", status.get("state") == "shut off",
                          "unexpected domain state needs explicit local review")
            steps.run(label + " normal start", ["start", instance])
        deadline = time.monotonic() + args.boot_timeout
        while True:
            readiness = steps.run(label + " normal boot readiness", [
                "run", instance, "--timeout", "10", "--", "true",
            ], timeout=30, retry=True)
            if not readiness.get("retry"):
                break
            if time.monotonic() >= deadline:
                raise DemoError(label + ": normal boot readiness timeout")
            time.sleep(2)
        result = steps.run(label + " nonroot Python task", ["run", instance, "--", "python3",
                           "/workspace/project/check.py"])
        steps.require(label + " task observation", "QSX_TASK_PASS_NONROOT" in result.get("stdout", ""),
                      "synthetic mathematical check and nonroot UID executed inside this guest")
    first, second = instances
    steps.run("write only inside first guest", ["run", first, "--", "sh", "-c",
              "printf '%s\\n' 'private-a' > /workspace/project/only-a.txt"])
    steps.run("second guest remains independent", ["run", second, "--", "test", "!", "-e",
              "/workspace/project/only-a.txt"])
    steps.require("base unchanged after guest writes", _sha256(Path(state["base_image"])) == state["base_sha256"],
                  "SHA-256 of retained standalone image is unchanged")
    source = Path(state["work_dir"]) / "source"
    steps.require("original source unchanged", _sha256(source / "check.py") == state["source_sha256"]
                  and not (source / "only-a.txt").exists(), "source directory was not guest storage")
    for instance in instances:
        steps.run("cut egress while retaining console", ["network-cut", instance])
        result = steps.run("serial works after cut", ["run", instance, "--", "sh", "-c",
                           "test \"$(id -u)\" -ne 0 && echo QSX_SERIAL_AFTER_CUT"])
        steps.require("serial after cut observation", "QSX_SERIAL_AFTER_CUT" in result.get("stdout", ""),
                      "local serial command still executes after host-side network-cut")
    if args.interactive:
        for command, key in (("console", "interactive_console"), ("agent", "interactive_agent")):
            print(f"Opening {command}; exit explicitly to continue. No provider login is attempted.")
            interactive_result = subprocess.run([steps.executable, command, first], check=False)
            steps.require(command + " process completed", interactive_result.returncode == 0,
                          "interactive local session returned; not proof of model work")
            steps.data[key] = "OPERATOR_INTERACTIVE_PROCESS_COMPLETED"
    for instance in instances:
        steps.run("stop instance", ["stop", instance])
    steps.data["status"] = "PASS_LOCAL_DEMO_WITH_EXPLICIT_EXCLUSIONS"
    state["phase"] = "COMPLETED"
    _save(Path(state["work_dir"]) / "demo-state.json", state)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.run and not args.continue_run:
        print(json.dumps({"mode": "PLAN_ONLY_NO_EXECUTION", "commands": command_plan(args),
                          "local_storage_gate": "--run --prepare-only, ACL review, then --continue",
                          "operator_login": "AWAITING_OPERATOR_LOGIN", "model_task": "NOT_RUN"}, indent=2))
        return 0
    steps = None
    try:
        if args.boot_timeout < 1:
            raise DemoError("--boot-timeout must be positive")
        executable = shutil.which(args.qsx)
        if executable is None:
            raise DemoError("installed qsx-alpine executable unavailable; use --qsx /absolute/path")
        if args.continue_run:
            if args.work_dir is None or args.prepare_only:
                raise DemoError("--continue requires --work-dir and cannot use --prepare-only")
            work = args.work_dir.expanduser().resolve()
            state = json.loads((work / "demo-state.json").read_text())
            if state.get("schema") != MARKER or state.get("phase") != "PREPARED" or state.get("work_dir") != str(work):
                raise DemoError("continuation requires this script's matching PREPARED demo-state.json")
            prior = json.loads((work / "demo-report.json").read_text())
            steps = Steps(executable, work / "demo-report.json", prior)
        else:
            work, cache = _fresh_directory(args)
            steps = Steps(executable, work / "demo-report.json")
            state = prepare(args, steps, work, cache)
        if args.prepare_only:
            steps.data["status"] = "AWAITING_HOST_STORAGE_ACCESS"
            steps.data["instance_manifests"] = state["instances"]
            steps.data["continue_command"] = [sys.executable, str(Path(__file__).resolve()),
                                               "--continue", "--work-dir", str(work), "--qsx", executable]
            print(json.dumps({"status": steps.data["status"], "instances": state["instances"],
                              "continue_command": steps.data["continue_command"]}, indent=2))
        else:
            execute_guests(args, steps, state)
            print(json.dumps({"status": steps.data["status"], "report": str(steps.report),
                              "operator_login": steps.data["operator_login"], "model_task": "NOT_RUN"}, indent=2))
        _save(steps.report, steps.data)
        return 0
    except (DemoError, OSError, ValueError, KeyError) as exc:
        if steps is not None:
            steps.data["status"], steps.data["error"] = "FAIL", str(exc)
            _save(steps.report, steps.data)
        print(f"demo_debian: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
