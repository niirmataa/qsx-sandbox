from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from .backend import LibvirtBackend, domain_xml
from .config import Config, load, validate_checkpoint_id
from .disk import Checkpoints
from .errors import QSXError
from .memory import ResearchMemory
from .provision import GuestProvisioner, OfflineGuestExport, inspect_offline_workspace
from .util import atomic_json
from .workspace import export_project, import_project, resume_task


def _checkpoint_task(config: Config, checkpoint_id: str) -> str:
    validate_checkpoint_id(checkpoint_id)
    manifest = json.loads(
        (config.state_dir / "checkpoints" / checkpoint_id / "manifest.json").read_text()
    )
    return str(manifest["task_id"])


def _only_task(config: Config) -> str:
    tasks = sorted((config.state_dir / "tasks").glob("*/task.json"))
    if len(tasks) != 1:
        raise QSXError("lifecycle command requires exactly one configured task")
    return str(json.loads(tasks[0].read_text())["task_id"])


def doctor() -> dict[str, object]:
    def tool(name: str, version: list[str]) -> dict[str, object]:
        path = shutil.which(name)
        if not path:
            return {"status": "BLOCKED", "detail": "not found"}
        result = subprocess.run([path, *version], capture_output=True, text=True, check=False)
        return {
            "status": "PASS" if result.returncode == 0 else "FAIL",
            "detail": (result.stdout or result.stderr).splitlines()[0],
        }

    kvm = Path("/dev/kvm")
    return {
        "schema_version": 1,
        "scope": "current-host-read-only",
        "python": sys.version.split()[0],
        "system": platform.system(),
        "architecture": platform.machine(),
        "filesystem": {
            "cwd_writable": os.access(Path.cwd(), os.W_OK),
            "free_bytes": shutil.disk_usage(Path.cwd()).free,
        },
        "tools": {
            "git": tool("git", ["--version"]),
            "qemu_img": tool("qemu-img", ["--version"]),
            "qemu": tool("qemu-system-x86_64", ["--version"]),
            "libvirt_client": tool("virsh", ["--version"]),
        },
        "kvm": {
            "status": "PASS" if kvm.exists() and os.access(kvm, os.R_OK | os.W_OK) else "BLOCKED",
            "detail": "/dev/kvm read/write" if kvm.exists() else "/dev/kvm absent",
        },
        "host_network_isolation": {"status": "NOT_RUN"},
        "host_acceptance": {"status": "NOT_RUN"},
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qsx", description="QSX single-VM controller")
    p.add_argument("--config", type=Path, default=Path("qsx.toml"))
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="read-only capability report")
    for name, help_text in (
        ("create", "create private disk and import a task"),
        ("resume", "resume the existing task branch"),
        ("checkpoint", "make a cold QCOW2 checkpoint"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("task_id")
    rollback = sub.add_parser("rollback", help="restore a cold checkpoint")
    rollback.add_argument("checkpoint_id")
    rollback.add_argument("--no-rescue", action="store_true")
    rollback.add_argument("--skip-collect", action="store_true")
    rollback.add_argument("--operation-id")
    export = sub.add_parser("export", help="export project to a new directory")
    export.add_argument("task_id")
    export.add_argument("destination", type=Path)
    sub.add_parser("domain-xml", help="print conservative libvirt domain XML")
    sub.add_parser("define", help="define the provisioned libvirt domain")
    sub.add_parser("start", help="start through KVM (never implicit TCG)")
    sub.add_parser("status", help="show libvirt domain state")
    shutdown = sub.add_parser("shutdown", help="cleanly stop the domain")
    shutdown.add_argument("--timeout", type=int, default=120)
    sub.add_parser("finalize-import", help="detach the import medium while stopped")
    sub.choices["finalize-import"].add_argument("task_id")
    record = sub.add_parser("memory-record", help="append a research/evidence record")
    record.add_argument("task_id")
    record.add_argument("kind", choices=["research", "evidence"])
    record.add_argument("status", choices=["PASS", "FAIL", "BLOCKED", "NOT_RUN"])
    record.add_argument("summary")
    record.add_argument("--details", default="")
    record.add_argument("--origin", choices=["agent", "operator", "controller"], default="agent")
    record.add_argument(
        "--evidence",
        choices=["agent_reported", "controller_observed", "independently_verified"],
        default="agent_reported",
    )
    record.add_argument("--git-commit")
    record.add_argument("--checkpoint-id")
    record.add_argument("--operation-id")
    record.add_argument("--step-id")
    record.add_argument("--claim-type", default="UNANNOTATED")
    record.add_argument("--applicability", default="unknown")
    record.add_argument("--checker")
    record.add_argument("--checker-version")
    record.add_argument("--input-sha256")
    record.add_argument("--output-sha256")
    render = sub.add_parser("memory-render", help="regenerate research memory views")
    render.add_argument("task_id")
    handoff = sub.add_parser("handoff", help="record a durable task handoff")
    handoff.add_argument("task_id")
    handoff.add_argument("summary")
    handoff.add_argument("--git-commit")
    handoff.add_argument("--checkpoint-id")
    handoff.add_argument("--output", type=Path)
    check = sub.add_parser("context-check", help="check a generated context bundle")
    check.add_argument("task_id")
    check.add_argument("bundle", type=Path)
    build_context = sub.add_parser("context-build", help="publish context to a new directory")
    build_context.add_argument("task_id")
    build_context.add_argument("output", type=Path)
    history = sub.add_parser("history-list", help="list controller research events")
    history.add_argument("task_id")
    collect = sub.add_parser("history-collect", help="collect untrusted outbox from stopped guest")
    collect.add_argument("task_id")
    sub.add_parser("history-reconcile", help="mark interrupted operations for reconciliation")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            print(json.dumps(doctor(), indent=2))
            return 0
        config = load(args.config)
        backend = LibvirtBackend()
        if args.command == "create":
            task = import_project(config, args.task_id)
            provision_path = config.state_dir / "tasks" / args.task_id / "provision.json"
            provision = (
                json.loads(provision_path.read_text())
                if provision_path.exists()
                else GuestProvisioner(config).provision(args.task_id)
            )
            xml_path = config.state_dir / "domain.xml"
            xml_path.write_text(
                domain_xml(config.name, config.disk, Path(provision["import_medium"]))
            )
            backend.define(xml_path)
            ResearchMemory(config).append(
                kind="lifecycle",
                task_id=args.task_id,
                summary="Import, provisioning and domain definition completed",
                status="PASS",
                origin="controller",
                evidence="controller_observed",
                operation_id=f"create:{task['run_id']}",
                claim_type="LIFECYCLE",
            )
            print(json.dumps({"task": task, "provision": provision, "domain": "defined"}, indent=2))
        elif args.command == "resume":
            task = resume_task(config, args.task_id)
            if backend.is_active(config.name):
                raise QSXError("BLOCKED: resume observation requires a SHUTOFF domain")
            memory = ResearchMemory(config)
            state = memory.observe_workspace(
                args.task_id, inspect_offline_workspace(config, args.task_id)
            )
            views = memory.render(args.task_id)
            print(
                json.dumps(
                    {
                        "status": "CURRENT",
                        "task": task,
                        "observed_state": state,
                        "context_manifest": str(views["manifest.json"]),
                    },
                    indent=2,
                )
            )
        elif args.command == "checkpoint":
            print(
                json.dumps(
                    Checkpoints(config, backend.is_active, inspect_offline_workspace).create(
                        args.task_id
                    ),
                    indent=2,
                )
            )
        elif args.command == "rollback":
            rollback_task = _checkpoint_task(config, args.checkpoint_id)
            if args.skip_collect:
                ResearchMemory(config).append(
                    kind="lifecycle",
                    task_id=rollback_task,
                    summary="Pre-rollback guest outbox collection explicitly skipped",
                    details="LOCAL_PENDING records are outside the controller durability guarantee.",
                    status="BLOCKED",
                    origin="operator",
                    evidence="controller_observed",
                    operation_id=(args.operation_id or str(uuid.uuid4())) + ":collection-gap",
                    claim_type="LIFECYCLE",
                )
            else:
                if backend.is_active(config.name):
                    raise QSXError("rollback collection requires a SHUTOFF domain")
                with tempfile.TemporaryDirectory(dir=config.state_dir) as temporary:
                    workspace = OfflineGuestExport().extract(
                        config.disk, config.guest_workspace, Path(temporary)
                    )
                    ResearchMemory(config).collect_outbox(
                        rollback_task,
                        workspace / ".qsx/outbox",
                    )
            Checkpoints(config, backend.is_active, inspect_offline_workspace).rollback(
                args.checkpoint_id, not args.no_rescue, args.operation_id
            )
        elif args.command == "export":
            if backend.is_active(config.name):
                raise QSXError("controlled export requires a SHUTOFF domain")
            with tempfile.TemporaryDirectory(dir=config.state_dir) as temporary:
                workspace = OfflineGuestExport().extract(
                    config.disk, config.guest_workspace, Path(temporary)
                )
                export_project(config, args.task_id, args.destination, workspace)
        elif args.command == "domain-xml":
            print(domain_xml(config.name, config.disk))
        elif args.command == "define":
            backend.define(config.state_dir / "domain.xml")
        elif args.command == "start":
            task_id = _only_task(config)
            memory = ResearchMemory(config)
            operation_id = f"start:{uuid.uuid4()}"
            memory.begin_operation(operation_id, task_id, "start")
            try:
                backend.start(config.name)
                memory.append(
                    kind="lifecycle",
                    task_id=task_id,
                    summary="VM start accepted by libvirt",
                    status="PASS",
                    origin="controller",
                    evidence="controller_observed",
                    operation_id=operation_id,
                    claim_type="LIFECYCLE",
                )
                memory.finish_operation(operation_id, "COMPLETED")
            except BaseException:
                memory.finish_operation(operation_id, "FAILED")
                raise
        elif args.command == "status":
            status = backend.status(config.name)
            ResearchMemory(config).append(
                kind="lifecycle",
                task_id=_only_task(config),
                summary=f"Observed VM status: {status}",
                status="PASS",
                origin="controller",
                evidence="controller_observed",
                operation_id=f"status:{uuid.uuid4()}",
                claim_type="LIFECYCLE",
            )
            print(status)
        elif args.command == "shutdown":
            backend.shutdown(config.name, args.timeout)
            try:
                ResearchMemory(config).append(
                    kind="lifecycle",
                    task_id=_only_task(config),
                    summary="VM clean shutdown observed",
                    status="PASS",
                    origin="controller",
                    evidence="controller_observed",
                    operation_id=f"shutdown:{uuid.uuid4()}",
                    claim_type="LIFECYCLE",
                )
            except QSXError as exc:
                gap = config.state_dir / "audit-gaps" / f"shutdown-{uuid.uuid4()}.json"
                atomic_json(
                    gap, {"operation": "shutdown", "status": "RECONCILE", "detail": str(exc)}
                )
        elif args.command == "finalize-import":
            provision_path = config.state_dir / "tasks" / args.task_id / "provision.json"
            provision = json.loads(provision_path.read_text())
            medium = Path(provision["import_medium"])
            backend.detach_import_medium(config.name, medium)
            provision["import_medium_detached"] = True
            atomic_json(provision_path, provision)
        elif args.command == "memory-record":
            record = ResearchMemory(config).append(
                kind=args.kind,
                task_id=args.task_id,
                summary=args.summary,
                details=args.details,
                status=args.status,
                origin=args.origin,
                evidence=args.evidence,
                git_commit=args.git_commit,
                checkpoint_id=args.checkpoint_id,
                operation_id=args.operation_id,
                step_id=args.step_id,
                claim_type=args.claim_type,
                applicability=args.applicability,
                verification={
                    "checker": args.checker,
                    "checker_version": args.checker_version,
                    "input_sha256": args.input_sha256,
                    "output_sha256": args.output_sha256,
                }
                if any(
                    (
                        args.checker,
                        args.checker_version,
                        args.input_sha256,
                        args.output_sha256,
                    )
                )
                else None,
            )
            print(json.dumps(record, indent=2))
        elif args.command == "memory-render":
            print(
                json.dumps(
                    {
                        name: str(path)
                        for name, path in ResearchMemory(config).render(args.task_id).items()
                    },
                    indent=2,
                )
            )
        elif args.command == "handoff":
            memory = ResearchMemory(config)
            record = memory.append(
                kind="handoff",
                task_id=args.task_id,
                summary=args.summary,
                status="NOT_RUN",
                origin="operator",
                evidence="controller_observed",
                git_commit=args.git_commit,
                checkpoint_id=args.checkpoint_id,
            )
            if args.output:
                memory.export_bundle(args.task_id, args.output)
            print(json.dumps(record, indent=2))
        elif args.command == "context-check":
            print(
                json.dumps(ResearchMemory(config).check_bundle(args.task_id, args.bundle), indent=2)
            )
        elif args.command == "context-build":
            ResearchMemory(config).export_bundle(args.task_id, args.output)
        elif args.command == "history-list":
            print(
                json.dumps(
                    [r for r in ResearchMemory(config).records() if r["task_id"] == args.task_id],
                    indent=2,
                )
            )
        elif args.command == "history-collect":
            if backend.is_active(config.name):
                raise QSXError("collection requires a SHUTOFF domain")
            with tempfile.TemporaryDirectory(dir=config.state_dir) as temporary:
                workspace = OfflineGuestExport().extract(
                    config.disk, config.guest_workspace, Path(temporary)
                )
                records = ResearchMemory(config).collect_outbox(
                    args.task_id, workspace / ".qsx/outbox"
                )
            print(json.dumps(records, indent=2))
        elif args.command == "history-reconcile":
            print(json.dumps(ResearchMemory(config).reconcile(), indent=2))
        return 0
    except QSXError as exc:
        print(f"qsx: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
