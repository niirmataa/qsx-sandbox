from __future__ import annotations

import json
from pathlib import Path

import pytest

from qsx.config import Config
from qsx.errors import QSXError
from qsx.memory import ResearchMemory


@pytest.fixture
def memory(tmp_path: Path) -> ResearchMemory:
    source = tmp_path / "source"
    source.mkdir()
    config = Config("MathResearch", source, tmp_path / "controller", tmp_path / "disk.qcow2")
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
        git_commit="a" * 40,
    )
    verified = memory.append(
        kind="evidence",
        task_id="task-1",
        summary="Independent checker reproduced the negative result",
        status="PASS",
        origin="controller",
        evidence="independently_verified",
        git_commit="b" * 40,
        checkpoint_id="20260905T120000Z-1234abcd",
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
    assert "agent/agent_reported" in context
    assert "controller/independently_verified" in context
    assert all(
        (memory.root / f"views/task-1/{name}.md").is_file()
        for name in ("CHANGELOG", "MEMORY", "LIFE", "CONTEXT")
    )


def test_agent_cannot_self_classify_as_independently_verified(memory: ResearchMemory) -> None:
    with pytest.raises(QSXError, match="cannot be independently verified"):
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
    record = json.loads(memory.journal.read_text())
    record["summary"] = "rewritten"
    memory.journal.write_text(json.dumps(record) + "\n")
    with pytest.raises(QSXError, match="hash chain"):
        memory.records()
