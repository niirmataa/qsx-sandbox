from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from .config import Config, validate_checkpoint_id, validate_task
from .errors import QSXError
from .util import atomic_json, sha256

Status = Literal["PASS", "FAIL", "BLOCKED", "NOT_RUN"]
STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}
PROVENANCE = {
    "AGENT_REPORTED",
    "GUEST_REPORTED",
    "CONTROLLER_OBSERVED",
    "INDEPENDENTLY_CHECKED",
    "HUMAN_REVIEWED",
}
CLAIMS = {
    "ASSUMPTION",
    "CONJECTURE",
    "NUMERICAL_OBSERVATION",
    "LEMMA_CANDIDATE",
    "COUNTEREXAMPLE",
    "DECISION",
    "FORMAL_CHECK_RESULT",
    "LIFECYCLE",
    "UNANNOTATED",
}
EVENTS = {"research", "evidence", "lifecycle", "handoff", "correction", "operation"}
RENDERER_VERSION = 2
CONTEXT_BUDGET = 12 * 1024


class ResearchMemory:
    """Transactional controller ledger; hashes establish local consistency, not authenticity."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.root = config.state_dir / "history" / self._project_id()
        self.database = self.root / "ledger.sqlite3"
        self.journal = self.root / "events.jsonl"
        self.artifacts = self.root / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialize()
        self._migrate_legacy_tasks()

    def _project_id(self) -> str:
        identity_path = self.config.state_dir / "project-identity.json"
        if identity_path.exists():
            data = json.loads(identity_path.read_text())
            return str(data["project_id"])
        project_id = str(uuid.uuid4())
        atomic_json(
            identity_path,
            {
                "schema_version": 1,
                "project_id": project_id,
                "source_at_creation": str(self.config.source),
            },
        )
        return project_id

    @property
    def project_id(self) -> str:
        return self.root.name

    @property
    def environment_id(self) -> str:
        return str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"qsx:{self.config.name}:{self.config.state_dir}")
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                  controller_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_id TEXT NOT NULL UNIQUE, operation_id TEXT UNIQUE,
                  task_id TEXT NOT NULL, record_json TEXT NOT NULL,
                  previous_sha256 TEXT NOT NULL, record_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                  operation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
                  state TEXT NOT NULL CHECK(state IN ('INTENT','COMPLETED','FAILED','RECONCILE')),
                  started_at TEXT NOT NULL, finished_at TEXT, detail TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS artifacts (
                  artifact_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, media_type TEXT NOT NULL,
                  size INTEGER NOT NULL, sha256 TEXT NOT NULL, stored_path TEXT NOT NULL);
            """)

    def _task(self, task_id: str) -> dict[str, object]:
        validate_task(task_id)
        path = self.config.state_dir / "tasks" / task_id / "task.json"
        if not path.is_file():
            raise QSXError("memory task does not exist")
        return cast(dict[str, object], json.loads(path.read_text()))

    def append(
        self,
        *,
        kind: str,
        task_id: str,
        summary: str,
        status: Status,
        origin: str = "agent",
        evidence: str = "agent_reported",
        details: str = "",
        git_commit: str | None = None,
        checkpoint_id: str | None = None,
        operation_id: str | None = None,
        claim_type: str = "UNANNOTATED",
        provenance: str | None = None,
        step_id: str | None = None,
        evidence_refs: list[str] | None = None,
        supersedes: str | None = None,
        applicability: str = "unknown",
        verification: dict[str, str] | None = None,
        observed_source: str = "operator_cli",
    ) -> dict[str, object]:
        task = self._task(task_id)
        provenance = provenance or {
            "agent_reported": "AGENT_REPORTED",
            "controller_observed": "CONTROLLER_OBSERVED",
            "independently_verified": "INDEPENDENTLY_CHECKED",
        }.get(evidence, evidence)
        if (
            kind not in EVENTS
            or status not in STATUSES
            or provenance not in PROVENANCE
            or claim_type not in CLAIMS
        ):
            raise QSXError("invalid research memory classification")
        if not summary.strip() or len(summary.encode()) > 500 or len(details.encode()) > 10_000:
            raise QSXError("memory summary/details length is invalid")
        if re.search(
            r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[A-Za-z0-9]{20,})", summary + details
        ):
            raise QSXError("memory record matches the secret rejection policy")
        checkpoint_manifest_sha256 = (
            self._verify_checkpoint(task_id, checkpoint_id) if checkpoint_id else None
        )
        if git_commit:
            self._verify_commit(task_id, git_commit, checkpoint_id)
        refs = evidence_refs or []
        self._verify_artifacts(task_id, refs)
        if provenance == "INDEPENDENTLY_CHECKED":
            if observed_source != "operator_cli":
                raise QSXError("guest-supplied provenance cannot become independent checking")
            required = {"checker", "checker_version", "input_sha256", "output_sha256"}
            if not verification or required - verification.keys():
                raise QSXError(
                    "independent checking requires checker/version and input/output digests"
                )
        if supersedes and not self.get(supersedes):
            raise QSXError("superseded event does not exist")
        if operation_id and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", operation_id):
            raise QSXError("invalid operation ID")
        if operation_id:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT record_json FROM events WHERE operation_id=?", (operation_id,)
                ).fetchone()
            if existing:
                existing_record = cast(dict[str, object], json.loads(existing[0]))
                self._publish_portable()
                self.render(task_id)
                return existing_record
        with self._transaction() as connection:
            if operation_id:
                existing = connection.execute(
                    "SELECT record_json FROM events WHERE operation_id=?", (operation_id,)
                ).fetchone()
                if existing:
                    return cast(dict[str, object], json.loads(existing[0]))
            last = connection.execute(
                "SELECT record_sha256 FROM events ORDER BY controller_sequence DESC LIMIT 1"
            ).fetchone()
            previous = str(last[0]) if last else "0" * 64
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(controller_sequence),0)+1 FROM events"
                ).fetchone()[0]
            )

            record: dict[str, object] = {
                "schema_version": 2,
                "event_id": str(uuid.uuid4()),
                "project_id": self.project_id,
                "environment_id": self.environment_id,
                "task_id": task_id,
                "run_id": task["run_id"],
                "session_id": task.get("session_id", task["run_id"]),
                "lineage_id": self.active_state().get(
                    "lineage_id", task.get("lineage_id", task["run_id"])
                ),
                "step_id": step_id,
                "operation_id": operation_id,
                "event_type": kind,
                "received_at_utc": datetime.now(UTC).isoformat(),
                "source_timestamp": None,
                "controller_sequence": sequence,
                "authenticated_actor": None,
                "reported_actor": origin,
                "actor_kind": "operator" if observed_source == "operator_cli" else "guest",
                "observed_source": observed_source,
                "provenance": provenance,
                "execution_status": status,
                "claim_type": claim_type,
                "git_commit": git_commit,
                "branch": task["branch"],
                "checkpoint_id": checkpoint_id,
                "checkpoint_manifest_sha256": checkpoint_manifest_sha256,
                "workspace_state_ref": self.active_state().get("workspace_state_ref"),
                "evidence_refs": refs,
                "supersedes": supersedes,
                "payload": {
                    "summary": summary.strip(),
                    "details": details,
                    "applicability": applicability,
                    "verification": verification or {},
                    "unknown_fields": [
                        "question",
                        "assumptions",
                        "limitations",
                        "inputs_parameters",
                        "code_environment",
                    ],
                    "question": "unknown",
                    "assumptions": "unknown",
                    "method": "unknown",
                    "observation": details or "unknown",
                    "limitations": "unknown",
                    "inputs_parameters": "unknown",
                    "code_environment": "unknown",
                },
                "previous_sha256": previous,
            }
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            record_hash = hashlib.sha256(canonical).hexdigest()
            record["record_sha256"] = record_hash
            encoded = json.dumps(record, sort_keys=True)
            connection.execute(
                "INSERT INTO events(event_id,operation_id,task_id,record_json,previous_sha256,record_sha256) VALUES(?,?,?,?,?,?)",
                (record["event_id"], operation_id, task_id, encoded, previous, record_hash),
            )
        self._publish_portable()
        self.render(task_id)
        return record

    def _migrate_legacy_tasks(self) -> None:
        tasks = self.config.state_dir / "tasks"
        if not tasks.is_dir():
            return
        for metadata in sorted(tasks.glob("*/task.json")):
            task = cast(dict[str, object], json.loads(metadata.read_text()))
            if "project_id" in task and "lineage_id" in task and "session_id" in task:
                continue
            task_id = str(task.get("task_id", metadata.parent.name))
            with self._connect() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM events WHERE operation_id=?",
                    (f"migration:legacy-task:{task_id}",),
                ).fetchone()
            if exists:
                continue
            self.append(
                kind="lifecycle",
                task_id=task_id,
                summary="Legacy task metadata registered",
                details="MIGRATED/RECONSTRUCTED: historical reasons, sessions and unobserved events remain unknown.",
                status="NOT_RUN",
                origin="controller",
                evidence="controller_observed",
                operation_id=f"migration:legacy-task:{task_id}",
                claim_type="UNANNOTATED",
                applicability="metadata known at migration time only",
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def records(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT record_json FROM events ORDER BY controller_sequence"
            ).fetchall()
        records = [cast(dict[str, object], json.loads(row[0])) for row in rows]
        self._verify_chain(records)
        return records

    def get(self, event_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
        return cast(dict[str, object], json.loads(row[0])) if row else None

    def verify(self, anchor: Path | None = None) -> dict[str, object]:
        records = self.records()
        head = str(records[-1]["record_sha256"]) if records else "0" * 64
        result: dict[str, object] = {
            "local_chain_consistency": "PASS",
            "sequence": len(records),
            "head_sha256": head,
            "authenticity": "NOT_ESTABLISHED",
            "completeness": "NOT_ESTABLISHED",
        }
        if anchor:
            reference = json.loads(anchor.read_text())
            result["anchor_match"] = (
                "PASS"
                if reference.get("head_sha256") == head
                and reference.get("sequence") == len(records)
                else "FAIL"
            )
            result["authenticity"] = "NOT_ESTABLISHED_UNSIGNED_ANCHOR"
        else:
            result["anchor_match"] = "NOT_RUN"
        return result

    def recover_from_portable(self, journal: Path) -> dict[str, object]:
        """Restore an empty ledger from an independently copied JSONL representation."""
        if self.records():
            raise QSXError("recovery requires an empty ledger")
        try:
            records = [
                cast(dict[str, object], json.loads(line))
                for line in journal.read_text(encoding="utf-8").splitlines()
            ]
        except (OSError, json.JSONDecodeError) as exc:
            raise QSXError("portable journal cannot be read") from exc
        self._verify_chain(records)
        if any(record.get("project_id") != self.project_id for record in records):
            raise QSXError("portable journal belongs to another project")
        with self._transaction() as connection:
            for record in records:
                connection.execute(
                    "INSERT INTO events(controller_sequence,event_id,operation_id,task_id,record_json,previous_sha256,record_sha256) VALUES(?,?,?,?,?,?,?)",
                    (
                        record["controller_sequence"],
                        record["event_id"],
                        record.get("operation_id"),
                        record["task_id"],
                        json.dumps(record, sort_keys=True),
                        record["previous_sha256"],
                        record["record_sha256"],
                    ),
                )
        missing = sorted(
            {
                str(ref)
                for record in records
                for ref in cast(list[object], record.get("evidence_refs", []))
            }
        )
        self._publish_portable()
        return {"status": "PASS", "records": len(records), "missing_artifacts": missing}

    def _verify_chain(self, records: list[dict[str, object]]) -> None:
        previous = "0" * 64
        for record in records:
            supplied = record.pop("record_sha256")
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            actual = hashlib.sha256(canonical).hexdigest()
            record["record_sha256"] = supplied
            if actual != supplied or record["previous_sha256"] != previous:
                raise QSXError("research memory local hash chain is inconsistent")
            previous = str(supplied)

    def begin_operation(self, operation_id: str, task_id: str, kind: str) -> str:
        self._task(task_id)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT state FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row:
                return str(row[0])
            connection.execute(
                "INSERT INTO operations VALUES(?,?,?,?,?,NULL,'')",
                (operation_id, task_id, kind, "INTENT", datetime.now(UTC).isoformat()),
            )
        return "INTENT"

    def finish_operation(self, operation_id: str, state: str, detail: str = "") -> None:
        if state not in {"COMPLETED", "FAILED", "RECONCILE"}:
            raise QSXError("invalid operation terminal state")
        with self._transaction() as connection:
            changed = connection.execute(
                "UPDATE operations SET state=?,finished_at=?,detail=? WHERE operation_id=?",
                (state, datetime.now(UTC).isoformat(), detail[:1000], operation_id),
            ).rowcount
            if changed != 1:
                raise QSXError("operation intent does not exist")

    def reconcile(self) -> list[dict[str, str]]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT operation_id,task_id,kind FROM operations WHERE state='INTENT'"
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE operations SET state='RECONCILE',finished_at=?,detail='completion unknown after interruption' WHERE operation_id=?",
                    (datetime.now(UTC).isoformat(), row[0]),
                )
        return [
            {"operation_id": row[0], "task_id": row[1], "kind": row[2], "state": "RECONCILE"}
            for row in rows
        ]

    def add_artifact(self, task_id: str, source: Path, media_type: str) -> dict[str, object]:
        self._task(task_id)
        if not source.is_file() or source.is_symlink() or source.stat().st_size > 50 * 1024 * 1024:
            raise QSXError("artifact must be a regular file no larger than 50 MiB")
        if source.name in {"auth.json", "id_rsa", "id_ed25519"}:
            raise QSXError("artifact name is blocked by the credential policy")
        artifact_hash = sha256(source)
        artifact_id = f"sha256:{artifact_hash}"
        self.artifacts.mkdir(parents=True, mode=0o700, exist_ok=True)
        target = self.artifacts / artifact_hash
        if not target.exists():
            shutil.copyfile(source, target)
            os.chmod(target, 0o600)
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?,?)",
                (
                    artifact_id,
                    task_id,
                    media_type,
                    source.stat().st_size,
                    artifact_hash,
                    str(target),
                ),
            )
        return {
            "artifact_id": artifact_id,
            "task_id": task_id,
            "media_type": media_type,
            "size": source.stat().st_size,
            "sha256": artifact_hash,
        }

    def collect_outbox(self, task_id: str, outbox: Path) -> list[dict[str, object]]:
        """Collect bounded guest JSON as untrusted reports; fields cannot grant provenance."""
        self._task(task_id)
        if not outbox.is_dir():
            return []
        files = sorted(outbox.glob("*.json"))
        if len(files) > 100:
            raise QSXError("guest outbox exceeds 100-record collection limit")
        collected = []
        for path in files:
            if path.is_symlink() or path.stat().st_size > 64 * 1024:
                raise QSXError("guest outbox record is unsafe or too large")
            try:
                report = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise QSXError("guest outbox record is invalid") from exc
            operation_id = report.get("operation_id")
            if not isinstance(operation_id, str):
                raise QSXError("guest outbox record requires a stable operation_id")
            status = report.get("execution_status", "NOT_RUN")
            if status not in STATUSES:
                raise QSXError("guest outbox execution status is invalid")
            collected.append(
                self.append(
                    kind=str(report.get("event_type", "research")),
                    task_id=task_id,
                    summary=str(report.get("summary", "UNANNOTATED")),
                    details=str(report.get("details", "")),
                    status=cast(Status, status),
                    origin=str(report.get("reported_actor", "untrusted-guest")),
                    provenance="GUEST_REPORTED",
                    observed_source="offline_guest_outbox",
                    operation_id=operation_id,
                    claim_type=str(report.get("claim_type", "UNANNOTATED")),
                    step_id=str(report["step_id"]) if report.get("step_id") else None,
                    applicability=str(report.get("applicability", "unknown")),
                )
            )
        return collected

    def export_bundle(self, task_id: str, destination: Path) -> None:
        """Publish only this task's portable memory and referenced approved artifacts."""
        if destination.exists():
            raise QSXError("research export destination already exists")
        views = self.render(task_id)
        temporary = destination.with_name(f".{destination.name}-{uuid.uuid4().hex}.tmp")
        temporary.mkdir(parents=True, mode=0o700)
        try:
            for path in views.values():
                if path.name != "manifest.json":
                    shutil.copy2(path, temporary / path.name)
            records = [record for record in self.records() if record["task_id"] == task_id]
            with (temporary / "events.jsonl").open("w", encoding="utf-8") as output:
                for record in records:
                    output.write(json.dumps(record, sort_keys=True) + "\n")
            referenced = sorted(
                {
                    str(ref)
                    for record in records
                    for ref in cast(list[object], record["evidence_refs"])
                }
            )
            artifact_manifests: list[dict[str, object]] = []
            if referenced:
                artifact_dir = temporary / "artifacts"
                artifact_dir.mkdir()
                with self._connect() as connection:
                    for ref in referenced:
                        row = connection.execute(
                            "SELECT task_id,media_type,size,sha256,stored_path FROM artifacts WHERE artifact_id=?",
                            (ref,),
                        ).fetchone()
                        if not row or row[0] != task_id or not Path(row[4]).is_file():
                            artifact_manifests.append(
                                {"artifact_id": ref, "availability": "MISSING"}
                            )
                            continue
                        shutil.copy2(row[4], artifact_dir / row[3])
                        artifact_manifests.append(
                            {
                                "artifact_id": ref,
                                "availability": "PRESENT",
                                "media_type": row[1],
                                "size": row[2],
                                "sha256": row[3],
                            }
                        )
            view_manifest = json.loads(views["manifest.json"].read_text())
            view_manifest["artifacts"] = artifact_manifests
            atomic_json(temporary / "manifest.json", view_manifest)
            os.replace(temporary, destination)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def check_bundle(self, task_id: str, bundle: Path) -> dict[str, object]:
        manifest_path = bundle / "manifest.json"
        if not manifest_path.is_file():
            return {"status": "BLOCKED", "reason": "manifest missing"}
        manifest = cast(dict[str, object], json.loads(manifest_path.read_text()))
        records = self.records()
        revision = str(records[-1]["record_sha256"]) if records else "0" * 64
        reasons = []
        if manifest.get("project_id") != self.project_id or manifest.get("task_id") != task_id:
            reasons.append("project/task mismatch")
        if manifest.get("journal_revision") != revision:
            reasons.append("journal revision changed")
        if manifest.get("active_state") != self.active_state():
            reasons.append("observed workspace/checkpoint state changed")
        return {"status": "STALE" if reasons else "CURRENT", "reasons": reasons}

    def _verify_artifacts(self, task_id: str, refs: list[str]) -> None:
        with self._connect() as connection:
            for ref in refs:
                row = connection.execute(
                    "SELECT task_id,size,sha256,stored_path FROM artifacts WHERE artifact_id=?",
                    (ref,),
                ).fetchone()
                if not row or row[0] != task_id:
                    raise QSXError(f"artifact is unavailable for task: {ref}")
                path = Path(row[3])
                if not path.is_file() or path.stat().st_size != row[1] or sha256(path) != row[2]:
                    raise QSXError(f"artifact integrity failed: {ref}")

    def _verify_checkpoint(self, task_id: str, checkpoint_id: str) -> str:
        validate_checkpoint_id(checkpoint_id)
        manifest = self.config.state_dir / "checkpoints" / checkpoint_id / "manifest.json"
        if not manifest.is_file():
            raise QSXError("referenced checkpoint is unavailable")
        data = json.loads(manifest.read_text())
        if data.get("task_id") != task_id or data.get("checkpoint_id") != checkpoint_id:
            raise QSXError("checkpoint does not belong to this task")
        return sha256(manifest)

    def _verify_commit(self, task_id: str, commit: str, checkpoint_id: str | None) -> None:
        if not re.fullmatch(r"[a-f0-9]{40,64}", commit):
            raise QSXError("Git commit must be a full hexadecimal object ID")
        if checkpoint_id:
            manifest = json.loads(
                (
                    self.config.state_dir / "checkpoints" / checkpoint_id / "manifest.json"
                ).read_text()
            )
            if manifest.get("git_commit") != commit:
                raise QSXError("Git commit does not match checkpoint evidence")
            return
        workspace = self.config.state_dir / "tasks" / task_id / "workspace"
        import subprocess

        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=workspace,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise QSXError("referenced Git commit is unavailable for this task")

    def active_state(self) -> dict[str, object]:
        path = self.config.state_dir / "checkpoint-state.json"
        if not path.exists():
            return {}
        return cast(dict[str, object], json.loads(path.read_text()))

    def observe_workspace(
        self, task_id: str, observation: tuple[str, str, bool, str | None, str]
    ) -> dict[str, object]:
        task = self._task(task_id)
        branch, commit, dirty, dirty_hash, workspace_hash = observation
        if branch != task["branch"]:
            raise QSXError("STALE: observed guest branch does not match the task branch")
        state = self.active_state()
        state.update(
            {
                "lineage_id": state.get("lineage_id", task.get("lineage_id", task["run_id"])),
                "branch": branch,
                "git_commit": commit,
                "dirty": dirty,
                "dirty_manifest_sha256": dirty_hash,
                "workspace_state_ref": f"workspace-manifest-v1:sha256:{workspace_hash}",
                "observed_at_utc": datetime.now(UTC).isoformat(),
            }
        )
        atomic_json(self.config.state_dir / "checkpoint-state.json", state)
        return state

    def render(self, task_id: str, budget: int = CONTEXT_BUDGET) -> dict[str, Path]:
        lock_path = self.root / ".render.lock"
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._render_unlocked(task_id, budget)

    def _render_unlocked(self, task_id: str, budget: int) -> dict[str, Path]:
        records = [record for record in self.records() if record["task_id"] == task_id]
        revision = str(records[-1]["record_sha256"]) if records else "0" * 64
        target = self.root / "views" / task_id
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = Path(tempfile.mkdtemp(prefix=f".{task_id}-", dir=parent))
        try:
            events = temporary / "events"
            events.mkdir()
            for record in records:
                atomic_json(events / f"{record['event_id']}.json", record)
            lines = [_line(record) for record in records]
            (temporary / "CHANGELOG.md").write_text("# CHANGELOG\n\n" + "\n".join(lines) + "\n")
            (temporary / "MEMORY.md").write_text(
                "# MEMORY\n\n"
                + "\n".join(
                    _line(r)
                    for r in records
                    if r["event_type"] in {"research", "evidence", "correction"}
                )
                + "\n"
            )
            (temporary / "LIFE.md").write_text(
                "# LIFE\n\n"
                + "\n".join(
                    _line(r)
                    for r in records
                    if r["event_type"] in {"lifecycle", "handoff", "operation"}
                )
                + "\n"
            )
            context, omitted = self._context(task_id, records, revision, budget)
            (temporary / "CONTEXT.md").write_text(context)
            manifest = {
                "schema_version": 1,
                "renderer_version": RENDERER_VERSION,
                "project_id": self.project_id,
                "environment_id": self.environment_id,
                "task_id": task_id,
                "journal_revision": revision,
                "event_count": len(records),
                "active_state": self.active_state(),
                "context_budget_bytes": budget,
                "omitted_context_records": omitted,
                "local_chain_consistency": "PASS",
                "authenticity": "NOT_ESTABLISHED_NO_INDEPENDENT_ANCHOR",
            }
            atomic_json(temporary / "manifest.json", manifest)
            marker = temporary / ".qsx-generated"
            marker.write_text("research-memory-v1\n")
            if target.exists():
                if not (target / ".qsx-generated").is_file():
                    raise QSXError("refusing to overwrite an unowned research directory")
                old = parent / f".{task_id}-old-{uuid.uuid4().hex}"
                os.replace(target, old)
                os.replace(temporary, target)
                shutil.rmtree(old)
            else:
                os.replace(temporary, target)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        return {
            name: target / name
            for name in ("CHANGELOG.md", "MEMORY.md", "LIFE.md", "CONTEXT.md", "manifest.json")
        }

    def _context(
        self, task_id: str, records: list[dict[str, object]], revision: str, budget: int
    ) -> tuple[str, int]:
        task = self._task(task_id)
        state = self.active_state()
        critical = [
            "# CONTEXT",
            "",
            f"Project: `{self.project_id}`",
            f"Task: `{task_id}`",
            f"Goal: {task.get('goal', 'UNANNOTATED')}",
            f"Success criterion: {task.get('success_criterion', 'UNANNOTATED')}",
            f"Active lineage: `{state.get('lineage_id', task.get('lineage_id', 'UNKNOWN'))}`",
            f"Checkpoint: `{state.get('current_checkpoint', 'UNKNOWN')}`",
            f"Journal revision: `{revision}`",
            f"Observed branch/HEAD/workspace: `{state.get('branch', 'UNKNOWN')}` / `{state.get('git_commit', 'UNKNOWN')}` / `{state.get('workspace_state_ref', 'UNKNOWN')}`",
            "Warnings: guest-local uncollected records are LOCAL_PENDING and are not rollback-safe; authenticity is NOT_ESTABLISHED without an independent anchor.",
            "",
            "## Selected records",
        ]
        base = "\n".join(critical) + "\n"
        if len(base.encode()) > budget:
            raise QSXError("context budget cannot contain critical warnings")
        selected: list[str] = []
        omitted = 0
        priority = sorted(
            records,
            key=lambda r: (
                r["execution_status"] not in {"FAIL", "BLOCKED", "NOT_RUN"},
                -cast(int, r["controller_sequence"]),
            ),
        )
        for record in priority:
            line = _line(record)
            if len((base + "\n".join(selected + [line]) + "\n").encode()) <= budget:
                selected.append(line)
            else:
                omitted += 1
        return base + "\n".join(
            selected
        ) + f"\n\nOmitted records due to budget: {omitted}. Full records: `events/`.\n", omitted

    def _publish_portable(self) -> None:
        with (self.root / ".publish.lock").open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self.records()
            temporary = self.journal.with_name(f".events-{uuid.uuid4().hex}.tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                for record in records:
                    stream.write(json.dumps(record, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.journal)


def _line(record: dict[str, object]) -> str:
    payload = cast(dict[str, object], record["payload"])
    links = [f"event [`{record['event_id']}`](events/{record['event_id']}.json)"]
    if record.get("git_commit"):
        links.append(f"git `{record['git_commit']}`")
    if record.get("checkpoint_id"):
        links.append(f"checkpoint `{record['checkpoint_id']}`")
    return (
        f"- **{record['execution_status']}** [{record['claim_type']}; {record['provenance']}] "
        f"{payload['summary']} ({', '.join(links)})"
    )
