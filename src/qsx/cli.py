from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .backend import LibvirtBackend, domain_xml
from .config import load
from .disk import Checkpoints
from .errors import QSXError
from .memory import ResearchMemory
from .provision import GuestProvisioner, OfflineGuestExport, inspect_offline_workspace
from .util import atomic_json
from .workspace import export_project, import_project, resume_task


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
    render = sub.add_parser("memory-render", help="regenerate research memory views")
    render.add_argument("task_id")
    handoff = sub.add_parser("handoff", help="record a durable task handoff")
    handoff.add_argument("task_id")
    handoff.add_argument("summary")
    handoff.add_argument("--git-commit")
    handoff.add_argument("--checkpoint-id")
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
            print(json.dumps({"task": task, "provision": provision, "domain": "defined"}, indent=2))
        elif args.command == "resume":
            print(json.dumps(resume_task(config, args.task_id), indent=2))
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
            Checkpoints(config, backend.is_active, inspect_offline_workspace).rollback(
                args.checkpoint_id, not args.no_rescue
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
            backend.start(config.name)
        elif args.command == "status":
            print(backend.status(config.name))
        elif args.command == "shutdown":
            backend.shutdown(config.name, args.timeout)
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
            print(
                json.dumps(
                    ResearchMemory(config).append(
                        kind="handoff",
                        task_id=args.task_id,
                        summary=args.summary,
                        status="NOT_RUN",
                        origin="operator",
                        evidence="controller_observed",
                        git_commit=args.git_commit,
                        checkpoint_id=args.checkpoint_id,
                    ),
                    indent=2,
                )
            )
        return 0
    except QSXError as exc:
        print(f"qsx: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
