from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .backend import LibvirtBackend, domain_xml
from .config import load
from .disk import Checkpoints, create_disk
from .errors import QSXError
from .workspace import export_project, import_project


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
            if not config.disk.exists():
                create_disk(config.disk, config.disk_gib)
            print(json.dumps(import_project(config, args.task_id), indent=2))
        elif args.command == "resume":
            print(json.dumps(import_project(config, args.task_id), indent=2))
        elif args.command == "checkpoint":
            print(json.dumps(Checkpoints(config, backend.is_active).create(args.task_id), indent=2))
        elif args.command == "rollback":
            Checkpoints(config, backend.is_active).rollback(args.checkpoint_id, not args.no_rescue)
        elif args.command == "export":
            export_project(config, args.task_id, args.destination)
        elif args.command == "domain-xml":
            print(domain_xml(config.name, config.disk))
        return 0
    except QSXError as exc:
        print(f"qsx: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
