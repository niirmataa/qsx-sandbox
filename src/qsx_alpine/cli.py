"""Standalone Alpine task images; no dependency on the archived QSX controller."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__, templates
from .errors import QSXError


def positive_seconds(value: str) -> int:
    seconds = int(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive number of seconds")
    return seconds


def doctor() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name in ("qemu-img", "qemu-system-x86_64", "xorriso", "virsh", "gpg", "cosign"):
        path = shutil.which(name)
        if path is None:
            results[name] = {"status": "BLOCKED", "reason": "not installed"}
            continue
        command = [path, "version"] if name == "cosign" else [path, "--version"]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=15,
                                       check=False)
            lines = (completed.stdout + completed.stderr).strip().splitlines()
            results[name] = {"status": "PASS" if completed.returncode == 0 else "FAIL",
                             "path": path, "version": lines[0] if lines else "",
                             "returncode": completed.returncode}
        except (OSError, subprocess.TimeoutExpired) as exc:
            results[name] = {"status": "BLOCKED", "reason": str(exc)}
    return {"schema_version": 1, "scope": "current-runner-read-only",
            "python": platform.python_version(), "architecture": platform.machine(),
            "tools": results, "kvm_device_present": Path("/dev/kvm").exists(),
            "kvm_access_hint": os.access("/dev/kvm", os.R_OK | os.W_OK),
            "kvm_execution": "NOT_RUN", "host_isolation": "NOT_RUN",
            "operator_login": "AWAITING_OPERATOR_LOGIN"}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qsx-alpine",
                                description="Build private Alpine sandboxes from task recipes")
    p.add_argument("--version", action="version", version="qsx-alpine " + __version__)
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="read-only capability inventory")
    create = commands.add_parser("template-new", help="write a new task recipe")
    create.add_argument("path", type=Path)
    create.add_argument("--name", required=True)
    create.add_argument("--agent-profile", type=Path, required=True)
    create.add_argument("--package", action="append", default=[])
    check = commands.add_parser("template-check", help="resolve and validate task+agent profile")
    check.add_argument("path", type=Path)
    listing = commands.add_parser("template-list")
    listing.add_argument("directory", type=Path)
    freeze = commands.add_parser("template-lock", help="explicitly freeze verified input artifacts")
    freeze.add_argument("path", type=Path)
    freeze.add_argument("--cache", type=Path, required=True)
    freeze.add_argument("--refresh", action="store_true")
    build = commands.add_parser("image-build", help="build from locked inputs, without guest networking")
    build.add_argument("lock", type=Path)
    build.add_argument("--cache", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--accelerator", choices=["kvm", "tcg"], default="kvm")
    build.add_argument("--timeout", type=positive_seconds, default=1800,
                       help="maximum guest build time in seconds (default: 1800)")
    build.add_argument("--policy", type=Path)
    verify = commands.add_parser("image-verify")
    verify.add_argument("manifest", type=Path)
    instance = commands.add_parser("instance-create", help="create an independent private instance")
    instance.add_argument("name")
    instance.add_argument("--image", type=Path, required=True)
    instance.add_argument("--state-root", type=Path, required=True)
    instance.add_argument("--source", type=Path)
    instance.add_argument("--policy", type=Path)
    for resource in ("memory-mib", "vcpus", "disk-gib"):
        instance.add_argument("--" + resource, type=int)
    for name in ("start", "status", "stop", "console", "finalize-import", "network-cut"):
        command = commands.add_parser(name)
        command.add_argument("instance", type=Path)
    for name in ("network-prepare", "network-grant"):
        command = commands.add_parser(name, help="explicit private instance egress configuration")
        command.add_argument("instance", type=Path)
        command.add_argument("--policy", type=Path, required=True)
    guest = commands.add_parser(
        "run", help="run argv as the nonroot agent through local serial",
        epilog="Put the guest command after --: run INSTANCE --timeout 60 -- python3 task.py",
    )
    guest.add_argument("instance", type=Path)
    guest.add_argument("--timeout", type=positive_seconds, default=120)
    agent = commands.add_parser("agent", help="launch the recipe's agent locally inside the guest")
    agent.add_argument("instance", type=Path)
    agent.add_argument("argv", nargs=argparse.REMAINDER)
    return p


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    guest_argv: list[str] = []
    if arguments[:1] == ["run"] and "--" in arguments:
        boundary = arguments.index("--")
        guest_argv = arguments[boundary + 1:]
        arguments = arguments[:boundary]
    args = parser().parse_args(arguments)
    result: Any
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command == "template-new":
            result = templates.create_recipe(args.path, args.name, args.package, args.agent_profile)
        elif args.command == "template-check":
            result = templates.load_recipe(args.path)
        elif args.command == "template-list":
            result = templates.list_templates(args.directory)
        elif args.command == "template-lock":
            lock = templates.freeze_recipe(args.path, args.cache, args.refresh)
            result = {"lock": str(lock)}
        elif args.command == "image-build":
            result = templates.build_image(args.lock, args.cache, args.output, args.accelerator,
                                           timeout=args.timeout, policy=args.policy)
        elif args.command == "image-verify":
            result = templates.verify_image(args.manifest)
        else:
            from . import runtime

            if args.command == "instance-create":
                resources = {key: getattr(args, key) for key in ("memory_mib", "vcpus", "disk_gib")
                             if getattr(args, key) is not None}
                result = runtime.create_instance(args.name, args.image, args.state_root,
                                                 args.source, resources, args.policy)
            elif args.command == "start":
                result = runtime.start_instance(args.instance)
            elif args.command == "status":
                result = runtime.status_instance(args.instance)
            elif args.command == "stop":
                result = runtime.stop_instance(args.instance)
            elif args.command == "finalize-import":
                result = runtime.finalize_import(args.instance)
            elif args.command == "network-prepare":
                result = runtime.prepare_network(args.instance, args.policy)
            elif args.command == "network-grant":
                result = runtime.grant_network(args.instance, args.policy)
            elif args.command == "network-cut":
                result = runtime.network_cut(args.instance)
            elif args.command == "console":
                runtime.console_instance(args.instance)
                return 0
            elif args.command == "agent":
                extra = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
                runtime.run_agent(args.instance, extra)
                return 0
            elif args.command == "run":
                if not guest_argv:
                    raise QSXError("run requires a guest command after --")
                result = runtime.run_guest(args.instance, guest_argv, args.timeout)
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return int(result["returncode"])
            else:
                raise QSXError("unknown operator command")
        if result is not None:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (QSXError, OSError, ValueError) as exc:
        print(f"qsx-alpine: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
