"""Minimal, dependency-free PEP 517 wheel builder for the pure-Python QSX package."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any

NAME = "qsx_alpine"
VERSION = "0.1.0"
DIST_INFO = f"{NAME}-{VERSION}.dist-info"


def _hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"sha256={digest}"


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    del config_settings, metadata_directory
    filename = f"{NAME}-{VERSION}-py3-none-any.whl"
    target = Path(wheel_directory) / filename
    files: dict[str, bytes] = {}
    for source in sorted(Path("src/qsx_alpine").glob("*.py")):
        files[f"qsx_alpine/{source.name}"] = source.read_bytes()
    files[f"{DIST_INFO}/METADATA"] = (
        f"Metadata-Version: 2.4\nName: {NAME}\nVersion: {VERSION}\n"
        "Summary: Standalone Alpine task image builder and runner\n"
        "Requires-Python: >=3.12\n"
    ).encode()
    files[f"{DIST_INFO}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: qsx-build\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    files[f"{DIST_INFO}/entry_points.txt"] = b"[console_scripts]\nqsx-alpine = qsx_alpine.cli:main\n"
    rows = [[path, _hash(data), str(len(data))] for path, data in files.items()]
    rows.append([f"{DIST_INFO}/RECORD", "", ""])
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    files[f"{DIST_INFO}/RECORD"] = output.getvalue().encode()
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as wheel:
        for path, data in files.items():
            wheel.writestr(path, data)
    return filename


def get_requires_for_build_wheel(config_settings: dict[str, Any] | None = None) -> list[str]:
    del config_settings
    return []
