from __future__ import annotations

import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from qsx.config import Config
from qsx.errors import QSXError
from qsx.memory import ResearchMemory
from qsx.workspace import import_project


@pytest.fixture
def memory(tmp_path: Path) -> ResearchMemory:
    source = tmp_path / "source"
    source.mkdir()
    (source / "proof.txt").write_text("synthetic")
    config = Config("MathResearch", source, tmp_path / "controller", tmp_path / "disk.qcow2")
    import_project(config, "task-1")
    import_project(config, "task")
    return ResearchMemory(config)


def test_negative_results_survive_and_context_is_current(memory: ResearchMemory) -> None:
    failed = memory.append(
        kind="research",
        task_id="task-1",
        summary="Counterexample search failed",
        details="Seed 7 produced no witness; this is not a proof.",
        status="FAIL",
        origin="agent",
        evidence="agent_reported",
    )
    verified = memory.append(
        kind="evidence",
        task_id="task-1",
        summary="Independent checker reproduced the negative result",
        status="PASS",
        origin="controller",
        evidence="independently_verified",
        verification={
            "checker": "synthetic-checker",
            "checker_version": "1.0",
            "input_sha256": "a" * 64,
            "output_sha256": "b" * 64,
        },
    )
    memory.append(
        kind="handoff",
        task_id="task-1",
        summary="Continue from lemma 4; retain failed search",
        status="NOT_RUN",
        origin="operator",
        evidence="controller_observed",
    )
    records = memory.records()
    assert [record["event_id"] for record in records[:2]] == [
        failed["event_id"],
        verified["event_id"],
    ]
    context = (memory.root / "views/task-1/CONTEXT.md").read_text()
    assert "Counterexample search failed" in context
    assert "Continue from lemma 4" in context
    assert "AGENT_REPORTED" in context
    assert "INDEPENDENTLY_CHECKED" in context
    assert all(
        (memory.root / f"views/task-1/{name}").is_file()
        for name in ("CHANGELOG.md", "MEMORY.md", "LIFE.md", "CONTEXT.md")
    )


def test_independent_verification_requires_real_metadata(memory: ResearchMemory) -> None:
    with pytest.raises(QSXError, match="requires checker"):
        memory.append(
            kind="evidence",
            task_id="task",
            summary="Unsupported verification claim",
            status="PASS",
            origin="agent",
            evidence="independently_verified",
        )


def test_hash_chain_detects_controller_journal_tampering(memory: ResearchMemory) -> None:
    memory.append(
        kind="research",
        task_id="task",
        summary="Observation",
        status="NOT_RUN",
        origin="agent",
        evidence="agent_reported",
    )
    record = json.loads(memory.journal.read_text().splitlines()[0])
    record["summary"] = "rewritten"
    with sqlite3.connect(memory.database) as connection:
        connection.execute(
            "UPDATE events SET record_json=? WHERE event_id=?",
            (json.dumps(record), record["event_id"]),
        )
    with pytest.raises(QSXError, match="hash chain"):
        memory.records()


def test_full_record_lookup_idempotency_and_correction(memory: ResearchMemory) -> None:
    first = memory.append(
        kind="research",
        task_id="task",
        summary="Numerical search",
        details="method=grid; seed=11; limitation=finite range",
        status="PASS",
        origin="agent",
        evidence="agent_reported",
        operation_id="collect:guest-note-1",
        claim_type="NUMERICAL_OBSERVATION",
        applicability="integers 1..100",
    )
    duplicate = memory.append(
        kind="research",
        task_id="task",
        summary="ignored duplicate payload",
        status="FAIL",
        operation_id="collect:guest-note-1",
    )
    assert duplicate["event_id"] == first["event_id"]
    assert memory.get(str(first["event_id"])) == first
    correction = memory.append(
        kind="correction",
        task_id="task",
        summary="Range statement corrected",
        status="PASS",
        origin="operator",
        evidence="controller_observed",
        supersedes=str(first["event_id"]),
        claim_type="DECISION",
    )
    assert len(memory.records()) == 2 and correction["supersedes"] == first["event_id"]
    full = memory.root / "views/task/events" / f"{first['event_id']}.json"
    assert "seed=11" in full.read_text()


def test_parallel_writes_and_recovery_are_explicit(memory: ResearchMemory) -> None:
    def append(index: int) -> str:
        return str(
            memory.append(
                kind="research",
                task_id="task",
                summary=f"parallel-{index}",
                status="NOT_RUN",
                operation_id=f"parallel:{index}",
            )["event_id"]
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(append, range(12)))) == 12
    assert [record["controller_sequence"] for record in memory.records()] == list(range(1, 13))
    assert memory.begin_operation("rollback:interrupted", "task", "rollback") == "INTENT"
    assert memory.reconcile() == [
        {
            "operation_id": "rollback:interrupted",
            "task_id": "task",
            "kind": "rollback",
            "state": "RECONCILE",
        }
    ]


def test_artifact_references_and_local_chain_are_not_authenticity(
    memory: ResearchMemory, tmp_path: Path
) -> None:
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("synthetic evidence")
    manifest = memory.add_artifact("task", artifact, "text/plain")
    memory.append(
        kind="evidence",
        task_id="task",
        summary="Evidence received, not independently checked",
        status="PASS",
        provenance="GUEST_REPORTED",
        observed_source="offline_guest_outbox",
        evidence_refs=[str(manifest["artifact_id"])],
    )
    assert memory.verify() == {
        "local_chain_consistency": "PASS",
        "sequence": 1,
        "head_sha256": memory.records()[-1]["record_sha256"],
        "authenticity": "NOT_ESTABLISHED",
        "completeness": "NOT_ESTABLISHED",
        "anchor_match": "NOT_RUN",
    }
    artifact_store = memory.artifacts / str(manifest["sha256"])
    artifact_store.write_text("tampered")
    with pytest.raises(QSXError, match="integrity failed"):
        memory.append(
            kind="evidence",
            task_id="task",
            summary="Must not bind tampered evidence",
            status="PASS",
            evidence_refs=[str(manifest["artifact_id"])],
        )


def test_guest_cannot_promote_its_pass_to_independent(memory: ResearchMemory) -> None:
    with pytest.raises(QSXError, match="guest-supplied"):
        memory.append(
            kind="evidence",
            task_id="task",
            summary="guest says PASS",
            status="PASS",
            provenance="INDEPENDENTLY_CHECKED",
            observed_source="offline_guest_outbox",
            verification={
                "checker": "claimed",
                "checker_version": "claimed",
                "input_sha256": "a" * 64,
                "output_sha256": "b" * 64,
            },
        )


def test_context_budget_reports_omissions(memory: ResearchMemory) -> None:
    for index in range(20):
        memory.append(
            kind="research",
            task_id="task",
            summary=f"record-{index}-" + "ą" * 40,
            status="FAIL" if index == 0 else "PASS",
        )
    paths = memory.render("task", budget=1400)
    context = paths["CONTEXT.md"].read_text()
    manifest = json.loads(paths["manifest.json"].read_text())
    assert "authenticity is NOT_ESTABLISHED" in context
    assert "record-0" in context
    assert manifest["omitted_context_records"] > 0


def test_legacy_task_migration_is_explicit_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    config = Config("MathResearch", source, tmp_path / "controller", tmp_path / "disk.qcow2")
    task = config.state_dir / "tasks/legacy"
    task.mkdir(parents=True)
    (task / "task.json").write_text(
        json.dumps(
            {
                "task_id": "legacy",
                "run_id": "old-run",
                "branch": "agent/mathresearch/legacy",
            }
        )
    )
    first = ResearchMemory(config)
    second = ResearchMemory(config)
    assert len(second.records()) == 1
    assert first.records()[0]["execution_status"] == "NOT_RUN"
    assert "MIGRATED/RECONSTRUCTED" in str(first.records()[0]["payload"])


def test_observed_state_drives_context_and_branch_mismatch_is_stale(memory: ResearchMemory) -> None:
    task = memory._task("task")
    state = memory.observe_workspace(
        "task", (str(task["branch"]), "c" * 40, True, "d" * 64, "e" * 64)
    )
    manifest = json.loads(memory.render("task")["manifest.json"].read_text())
    assert manifest["active_state"] == state
    with pytest.raises(QSXError, match="STALE"):
        memory.observe_workspace("task", ("wrong", "c" * 40, False, None, "e" * 64))


def test_context_bundle_staleness_and_project_scoped_artifact_export(
    memory: ResearchMemory, tmp_path: Path
) -> None:
    artifact = tmp_path / "proof.log"
    artifact.write_text("approved synthetic proof log")
    reference = memory.add_artifact("task", artifact, "text/plain")
    memory.append(
        kind="evidence",
        task_id="task",
        summary="Guest log collected",
        status="PASS",
        provenance="GUEST_REPORTED",
        observed_source="offline_guest_outbox",
        evidence_refs=[str(reference["artifact_id"])],
        operation_id="collect:proof-log",
    )
    bundle = tmp_path / "bundle"
    memory.export_bundle("task", bundle)
    assert memory.check_bundle("task", bundle)["status"] == "CURRENT"
    assert (bundle / "artifacts" / str(reference["sha256"])).read_text() == artifact.read_text()
    memory.append(
        kind="research",
        task_id="task",
        summary="New revision",
        status="NOT_RUN",
        operation_id="note:new-revision",
    )
    assert memory.check_bundle("task", bundle)["status"] == "STALE"


def test_recover_portable_records_reports_missing_artifacts(
    memory: ResearchMemory, tmp_path: Path
) -> None:
    artifact = tmp_path / "result.dat"
    artifact.write_bytes(b"result")
    reference = memory.add_artifact("task", artifact, "application/octet-stream")
    memory.append(
        kind="evidence",
        task_id="task",
        summary="Recoverable record",
        status="PASS",
        evidence_refs=[str(reference["artifact_id"])],
    )
    backup = tmp_path / "events-backup.jsonl"
    shutil.copy2(memory.journal, backup)
    restored_state = tmp_path / "restored-controller"
    shutil.copytree(memory.config.state_dir / "tasks", restored_state / "tasks")
    shutil.copy2(
        memory.config.state_dir / "project-identity.json",
        restored_state / "project-identity.json",
    )
    restored = ResearchMemory(
        Config(
            "MathResearch",
            memory.config.source,
            restored_state,
            tmp_path / "restored.qcow2",
        )
    )
    result = restored.recover_from_portable(backup)
    assert result["records"] == 1
    assert result["missing_artifacts"] == [reference["artifact_id"]]


def test_guest_outbox_collection_is_idempotent_and_cannot_claim_authority(
    memory: ResearchMemory, tmp_path: Path
) -> None:
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "note.json").write_text(
        json.dumps(
            {
                "operation_id": "guest:note:1",
                "event_type": "research",
                "execution_status": "PASS",
                "summary": "Guest claims a result",
                "reported_actor": "administrator",
                "provenance": "INDEPENDENTLY_CHECKED",
                "claim_type": "NUMERICAL_OBSERVATION",
            }
        )
    )
    first = memory.collect_outbox("task", outbox)
    second = memory.collect_outbox("task", outbox)
    assert first[0]["event_id"] == second[0]["event_id"]
    assert len(memory.records()) == 1
    assert first[0]["provenance"] == "GUEST_REPORTED"
    assert first[0]["authenticated_actor"] is None
    assert first[0]["execution_status"] == "PASS"


def test_secret_like_memory_and_credential_artifact_are_rejected(
    memory: ResearchMemory, tmp_path: Path
) -> None:
    with pytest.raises(QSXError, match="secret rejection"):
        memory.append(
            kind="research",
            task_id="task",
            summary="accidentally included sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            status="NOT_RUN",
        )
    credential = tmp_path / "auth.json"
    credential.write_text("synthetic")
    with pytest.raises(QSXError, match="credential policy"):
        memory.add_artifact("task", credential, "application/json")


def test_retry_after_render_failure_republishes_without_duplicate(
    memory: ResearchMemory, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_render = memory.render
    calls = 0

    def interrupted(task_id: str, budget: int = 12 * 1024) -> dict[str, Path]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("controlled render interruption")
        return real_render(task_id, budget)

    monkeypatch.setattr(memory, "render", interrupted)
    with pytest.raises(OSError, match="controlled"):
        memory.append(
            kind="research",
            task_id="task",
            summary="Persist before render",
            status="NOT_RUN",
            operation_id="note:render-retry",
        )
    retried = memory.append(
        kind="research",
        task_id="task",
        summary="same operation retry",
        status="NOT_RUN",
        operation_id="note:render-retry",
    )
    assert len(memory.records()) == 1
    assert (memory.root / "views/task/CONTEXT.md").is_file()
    assert retried["payload"]["summary"] == "Persist before render"
