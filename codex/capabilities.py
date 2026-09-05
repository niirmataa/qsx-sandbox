#!/usr/bin/env python3
"""Read-only runner inventory. No secrets, hostnames, external requests or device opening."""
from __future__ import annotations
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def tool_version(name: str, argument: str = "--version") -> dict[str, object]:
    executable = shutil.which(name)
    if executable is None:
        return {"available": False}
    try:
        result = subprocess.run(
            [executable, argument], capture_output=True, text=True,
            timeout=10, check=False,
        )
        lines = (result.stdout + result.stderr).strip().splitlines()
        return {"available": True, "returncode": result.returncode,
                "version": lines[0][:300] if lines else ""}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "probe_error": type(exc).__name__}


def device_hint(path: str) -> dict[str, object]:
    p = Path(path)
    try:
        info = p.stat()
    except (OSError, ValueError):
        return {"exists": False, "usable": "NOT_TESTED"}
    return {"exists": True, "character_device": stat.S_ISCHR(info.st_mode),
            "readable_writable_hint": os.access(p, os.R_OK | os.W_OK),
            "usable": "NOT_TESTED"}


def main() -> None:
    release = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key in {"ID", "VERSION_ID"}:
                release[key] = value.strip('"')
    except OSError:
        pass
    tools = {name: tool_version(name) for name in (
        "git", "qemu-img", "qemu-system-x86_64", "virsh",
        "xmllint", "shellcheck", "make",
    )}
    output = {
        "schema_version": 1,
        "context": "runner_inventory_not_target_hardware",
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "operating_system": release,
        "tools": tools,
        "devices": {"kvm": device_hint("/dev/kvm"),
                    "tun": device_hint("/dev/net/tun")},
        "free_bytes_in_current_filesystem": shutil.disk_usage(Path.cwd()).free,
        "host_acceptance": "NOT_RUN",
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
