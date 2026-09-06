from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from .errors import QSXError

SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
DEFAULT_LIMITS = {"memory_mib": 4096, "vcpus": 4, "disk_gib": 64}
DEFAULT_RESOURCES = {"memory_mib": 2048, "vcpus": 2, "disk_gib": 8}
MINIMUMS = {"memory_mib": 256, "vcpus": 1, "disk_gib": 2}
MAXIMUMS = {"memory_mib": 1048576, "vcpus": 256, "disk_gib": 65536}


def load_policy(path: Path | None = None) -> dict[str, Any]:
    """Operator-owned policy, never read from the source project or recipe."""
    raw: dict[str, Any] = {}
    if path is not None:
        try:
            raw = tomllib.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise QSXError(f"cannot read operator policy: {exc}") from exc
    if set(raw) - {"schema_version", "limits", "network_profile"}:
        raise QSXError("unknown operator policy fields")
    if raw.get("schema_version", 1) != 1:
        raise QSXError("operator policy schema_version must be 1")
    if raw.get("network_profile", "offline") != "offline":
        raise QSXError(
            "an enforcing OpenAI transport is not implemented; only offline is supported"
        )
    limits = raw.get("limits", {})
    if not isinstance(limits, dict) or set(limits) - set(DEFAULT_LIMITS):
        raise QSXError("policy limits must contain memory_mib, vcpus and/or disk_gib")
    limits = {**DEFAULT_LIMITS, **limits}
    for name, value in limits.items():
        if type(value) is not int or not MINIMUMS[name] <= value <= MAXIMUMS[name]:
            raise QSXError(f"invalid operator limit: {name}")
    return {"schema_version": 1, "network_profile": "offline", "limits": limits}


def resolve_resources(
    requested: dict[str, Any], overrides: dict[str, int] | None, policy: dict[str, Any]
) -> dict[str, int]:
    values = {**DEFAULT_RESOURCES, **requested, **(overrides or {})}
    if set(values) != set(DEFAULT_RESOURCES):
        raise QSXError("unknown instance resource field")
    result: dict[str, int] = {}
    for name, value in values.items():
        if type(value) is not int or not MINIMUMS[name] <= value <= policy["limits"][name]:
            raise QSXError(f"requested {name} exceeds operator policy or is below the minimum")
        result[name] = value
    return result
