"""Reject QCOW2 host-file dependencies before invoking a native image parser."""

from __future__ import annotations

import json
import os
import stat
import struct
from pathlib import Path
from typing import Any

from .errors import QSXError
from .util import run


def check_header(path: Path) -> None:
    """Bounded admission check, not a replacement for qemu-img check.

    Images must remain stable in operator-owned storage during admission/use.
    No native parser is invoked until external-file references are rejected.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            initial = os.fstat(stream.fileno())
            if not stat.S_ISREG(initial.st_mode):
                raise QSXError("QCOW2 image must be an ordinary file")
            header = stream.read(72)
            if len(header) != 72 or header[:4] != b"QFI\xfb":
                raise QSXError("invalid or truncated QCOW2 header")
            version = struct.unpack_from(">I", header, 4)[0]
            if version not in (2, 3):
                raise QSXError("unsupported QCOW2 version")
            if struct.unpack_from(">Q", header, 8)[0]:
                raise QSXError("image must be standalone without a backing file")
            cluster_bits = struct.unpack_from(">I", header, 20)[0]
            if not 9 <= cluster_bits <= 21:
                raise QSXError("invalid QCOW2 cluster size")
            cluster_size = 1 << cluster_bits
            header_length = 72
            if version == 3:
                features = stream.read(32)
                if len(features) != 32:
                    raise QSXError("truncated QCOW2 feature header")
                incompatible, _, autoclear, _, header_length = struct.unpack(">QQQII", features)
                if incompatible & 4 or autoclear & 2:
                    raise QSXError("standalone QCOW2 cannot use an external data file")
                # Dirty/corrupt, compression and extended-L2 flags are understood
                # here; qemu-img check still decides structural consistency.
                if incompatible & ~0x1B:
                    raise QSXError("unknown QCOW2 incompatible features")
                if not 104 <= header_length <= cluster_size or header_length % 8:
                    raise QSXError("invalid QCOW2 header length")
            stream.seek(header_length)
            # Extensions cannot leave the first cluster (at most 2 MiB).
            extensions = stream.read(cluster_size - header_length)
            offset = 0
            while offset + 8 <= len(extensions):
                kind, length = struct.unpack_from(">II", extensions, offset)
                offset += 8
                if kind == 0:
                    if length:
                        raise QSXError("invalid QCOW2 extension terminator")
                    break
                if kind == 0x44415441:
                    raise QSXError("standalone QCOW2 cannot name an external data file")
                padded = (length + 7) & ~7
                if padded > len(extensions) - offset:
                    raise QSXError("truncated or oversized QCOW2 extension")
                offset += padded
            else:
                raise QSXError("QCOW2 extension terminator missing")
            final = os.fstat(stream.fileno())
            if (initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) != (
                final.st_size, final.st_mtime_ns, final.st_ctime_ns
            ):
                raise QSXError("QCOW2 changed during header inspection")
    except (OSError, ValueError, struct.error) as exc:
        raise QSXError(f"cannot inspect QCOW2 header: {exc}") from exc


def inspect_image(path: Path) -> dict[str, Any]:
    # Absolute paths cannot be interpreted as QEMU filename protocols, and
    # explicit format avoids probing another native image parser.
    path = path.absolute()
    check_header(path)
    try:
        info = json.loads(run(["qemu-img", "info", "-f", "qcow2", "--output=json", str(path)]))
    except ValueError as exc:
        raise QSXError(f"invalid QCOW2 inspection result: {exc}") from exc
    if not isinstance(info, dict):
        raise QSXError("invalid QCOW2 inspection object")
    specific = info.get("format-specific", {})
    details = specific.get("data", {}) if isinstance(specific, dict) else None
    if (info.get("format") != "qcow2" or not isinstance(details, dict)
            or any(info.get(key) for key in ("backing-filename", "full-backing-filename", "data-file"))
            or details.get("data-file")):
        raise QSXError("image must be a standalone QCOW2 without external files")
    run(["qemu-img", "check", "-f", "qcow2", str(path)])
    return info
