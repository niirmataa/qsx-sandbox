# Research Memory v1

The authoritative memory is `state_dir/research-memory/events.jsonl`, outside the QCOW2 disk. It is append-only through the CLI, mode 0600, fsync'd per event, and hash-chained. A VM rollback updates disk lineage but does not roll back this journal. Controller lifecycle records therefore retain both the abandoned and new checkpoint branches.

Records classify `kind`, PASS/FAIL/BLOCKED/NOT_RUN status, origin, evidence layer, task, optional full Git object ID, optional checkpoint ID, details, timestamp, and chain hashes. `agent_reported`, `controller_observed`, and `independently_verified` are distinct; an agent cannot label its own statement independently verified. Negative and unexecuted results are never filtered out.

Every append regenerates task-scoped `CHANGELOG.md`, `MEMORY.md`, `LIFE.md`, and `CONTEXT.md` under `research-memory/views/<task-id>`. CONTEXT includes the latest event, all negative/unresolved results, independently verified evidence, and latest handoff. Views are derived and atomically replaceable; the JSONL journal is authoritative.

Use:

```bash
qsx --config qsx.toml memory-record task-001 research FAIL "Search found no witness" --details "seed=7" --origin agent --evidence agent_reported --git-commit FULL_SHA
qsx --config qsx.toml memory-record task-001 evidence PASS "Checker reproduced result" --origin controller --evidence independently_verified --checkpoint-id CHECKPOINT_ID
qsx --config qsx.toml handoff task-001 "Continue at lemma 4" --git-commit FULL_SHA
qsx --config qsx.toml memory-render task-001
```

The controller only stores supplied text and hashes project bytes; it never imports or executes project Python, hooks, tests, or other project programs.
