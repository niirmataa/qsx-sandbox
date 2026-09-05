from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from .config import Config, validate_checkpoint_id, validate_task
from .errors import QSXError

Status = Literal["PASS", "FAIL", "BLOCKED", "NOT_RUN"]
Origin = Literal["agent", "controller", "operator"]
Evidence = Literal["agent_reported", "controller_observed", "independently_verified"]
KINDS = {"research", "evidence", "lifecycle", "handoff"}
STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}
ORIGINS = {"agent", "controller", "operator"}
EVIDENCE = {"agent_reported", "controller_observed", "independently_verified"}


class ResearchMemory:
    """Append-only, hash-chained controller memory stored outside the VM disk."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.root = config.state_dir / "research-memory"
        self.journal = self.root / "events.jsonl"

    def records(self) -> list[dict[str, object]]:
        if not self.journal.exists():
            return []
        records: list[dict[str, object]] = []
        previous = "0" * 64
        for number, line in enumerate(self.journal.read_text(encoding="utf-8").splitlines(), 1):
            try:
                record = cast(dict[str, object], json.loads(line))
            except json.JSONDecodeError as exc:
                raise QSXError(f"research memory is corrupt at line {number}") from exc
            supplied = record.pop("record_sha256", None)
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            actual = hashlib.sha256(canonical).hexdigest()
            if supplied != actual or record.get("previous_sha256") != previous:
                raise QSXError(f"research memory hash chain failed at line {number}")
            record["record_sha256"] = supplied
            previous = actual
            records.append(record)
        return records

    def append(
        self,
        *,
        kind: str,
        task_id: str,
        summary: str,
        status: Status,
        origin: Origin,
        evidence: Evidence,
        details: str = "",
        git_commit: str | None = None,
        checkpoint_id: str | None = None,
    ) -> dict[str, object]:
        validate_task(task_id)
        if (
            kind not in KINDS
            or status not in STATUSES
            or origin not in ORIGINS
            or evidence not in EVIDENCE
        ):
            raise QSXError("invalid research memory classification")
        if not summary.strip() or len(summary) > 500 or len(details) > 10_000:
            raise QSXError("memory summary/details length is invalid")
        if evidence == "independently_verified" and origin == "agent":
            raise QSXError("an agent declaration cannot be independently verified by itself")
        if checkpoint_id:
            validate_checkpoint_id(checkpoint_id)
        if git_commit and not re.fullmatch(r"[a-f0-9]{40,64}", git_commit):
            raise QSXError("Git commit must be a full hexadecimal object ID")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.journal, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                current = self.records()
                previous = str(current[-1]["record_sha256"]) if current else "0" * 64
                record: dict[str, object] = {
                    "schema_version": 1,
                    "sequence": len(current) + 1,
                    "event_id": str(uuid.uuid4()),
                    "timestamp": datetime.now(UTC).isoformat(),
                    "environment": self.config.name,
                    "task_id": task_id,
                    "kind": kind,
                    "summary": summary.strip(),
                    "details": details,
                    "status": status,
                    "origin": origin,
                    "evidence": evidence,
                    "git_commit": git_commit,
                    "checkpoint_id": checkpoint_id,
                    "previous_sha256": previous,
                }
                canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
                record["record_sha256"] = hashlib.sha256(canonical).hexdigest()
                stream.write(json.dumps(record, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            pass
        self.render(task_id)
        return record

    def render(self, task_id: str) -> dict[str, Path]:
        validate_task(task_id)
        records = [record for record in self.records() if record["task_id"] == task_id]
        views = self.root / "views" / task_id
        views.mkdir(parents=True, exist_ok=True, mode=0o700)
        changelog = ["# CHANGELOG", "", *(_line(record) for record in records)]
        memory = ["# MEMORY", "", "Agent reports are not independent verification.", ""]
        memory.extend(
            _line(record) for record in records if record["kind"] in {"research", "evidence"}
        )
        life = [
            "# LIFE",
            "",
            *(_line(record) for record in records if record["kind"] == "lifecycle"),
        ]
        latest = records[-1] if records else None
        negatives = [
            record for record in records if record["status"] in {"FAIL", "BLOCKED", "NOT_RUN"}
        ]
        context = ["# CONTEXT", "", f"Task: `{task_id}`", f"Events: {len(records)}"]
        if latest:
            context += [f"Latest: {_line(latest)}"]
        context += ["", "## Unresolved and negative results"]
        context.extend(_line(record) for record in negatives)
        verified = [record for record in records if record["evidence"] == "independently_verified"]
        if verified:
            context += ["", "## Independently verified evidence"]
            context.extend(_line(record) for record in verified)
        handoffs = [record for record in records if record["kind"] == "handoff"]
        if handoffs:
            context += ["", "## Latest handoff", _line(handoffs[-1])]
        result: dict[str, Path] = {}
        for name, lines in (
            ("CHANGELOG", changelog),
            ("MEMORY", memory),
            ("LIFE", life),
            ("CONTEXT", context),
        ):
            path = views / f"{name}.md"
            _atomic_text(path, "\n".join(lines) + "\n")
            result[name] = path
        return result


def _line(record: dict[str, object]) -> str:
    links = []
    if record.get("git_commit"):
        links.append(f"git `{record['git_commit']}`")
    if record.get("checkpoint_id"):
        links.append(f"checkpoint `{record['checkpoint_id']}`")
    suffix = f" ({', '.join(links)})" if links else ""
    return (
        f"- {record['timestamp']} **{record['status']}** [{record['kind']}; "
        f"{record['origin']}/{record['evidence']}] {record['summary']}{suffix}"
    )


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
