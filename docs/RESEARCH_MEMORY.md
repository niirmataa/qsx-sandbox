# Research Memory v1

## Guarantees and trust boundary

The authoritative ledger is standard-library SQLite at `state_dir/history/<project_id>/ledger.sqlite3`, outside the QCOW2. It uses `BEGIN IMMEDIATE`, WAL, `synchronous=FULL`, schema constraints, stable operation IDs, and controller ordering. JSONL is an atomically published portable representation. These mechanisms support concurrency, recovery, and **local chain consistency checks**. They do not make history immutable against the file owner/root, prove completeness without an external anchor, authenticate self-described actors, establish trusted time, or prove scientific claims.

`verify()` therefore reports authenticity and completeness as `NOT_ESTABLISHED`; even a matching unsigned copy is not a signature. Provisioning a signing key and independently storing a signed trust anchor are future operator steps. A portable evidence checkpoint would bind format/project/environment, sequence/head, state-manifest hashes, and an independently provisioned key ID. No signing is claimed in v1.

## Records and evidence

Records carry stable project/environment/task/run/session/lineage IDs, optional step/operation IDs, controller sequence/time, reported versus authenticated actor, observed source, execution status, claim type, provenance, branch/Git/checkpoint/workspace references, evidence references, supersession, applicability, full details, and explicit unknown research fields. `INDEPENDENTLY_CHECKED` requires checker/version and input/output digests and cannot originate in a guest package. A reported PASS remains reported.

Git references must exist in the task staging history or match a referenced checkpoint manifest. Checkpoints must exist and belong to the task; the event binds the checkpoint manifest hash. Approved artifacts are copied to controller storage with media type, size, and SHA-256; missing or modified artifacts fail verification. Hash possession is not evidence-file availability.

Guest outbox JSON is collected only from a stopped-disk extraction, bounded to 100 regular JSON files of 64 KiB each. The controlled channel assigns `GUEST_REPORTED`; payload actor/provenance claims grant no authority. A stable `operation_id` makes collection idempotent. Uncollected guest notes remain `LOCAL_PENDING`, may disappear on rollback, and are called out in CONTEXT.

## Views and context

A single ledger revision atomically publishes an owned task directory with `CHANGELOG.md`, `MEMORY.md`, `LIFE.md`, `CONTEXT.md`, `manifest.json`, and full `events/<event_id>.json` records. Summary lines link stable event IDs to full details. QSX refuses to replace a directory without its ownership marker.

CONTEXT records project/task identity, goal/success criterion (or `UNANNOTATED`), active lineage/checkpoint, observed branch/HEAD/dirty workspace reference, journal revision, critical LOCAL_PENDING/authenticity warnings, negative results, and handoff-relevant records. Its default 12 KiB UTF-8 budget never splits a record, prioritizes negative/unresolved records, reports omissions, and fails if critical warnings cannot fit. Full LIFE/events remain available.

`context-build` creates a new portable directory. `context-check` returns CURRENT or STALE when project/task, journal revision, or active state differs. `resume` requires a stopped guest, obtains a fresh read-only observation, validates its branch, and rebuilds context. Context transfer back into a guest remains an operator-controlled read-only medium; QSX never writes into an active QCOW2.

## Operations and recovery

Rollback writes a durable operation INTENT before disk replacement, then COMPLETED, FAILED, or RECONCILE. A retry with the same operation ID is idempotent. `history-reconcile` marks abandoned INTENT operations explicitly; it never invents completion. SQLite cannot atomically commit QCOW2, manifests, and Git together, and documentation does not claim otherwise. Emergency shutdown happens first; if ledger publication then fails, a controller audit-gap record is written for reconciliation.

Legacy task metadata produces one idempotent `MIGRATED/RECONSTRUCTED` event with unknown history—not invented sessions or reasons. `recover_from_portable` restores an empty ledger after chain/project validation and reports referenced artifacts as missing until restored separately.

## CLI

```bash
qsx --config qsx.toml history-list task-001
qsx --config qsx.toml history-collect task-001
qsx --config qsx.toml memory-record task-001 research FAIL "Search result" --operation-id note-17 --claim-type NUMERICAL_OBSERVATION --details "seed=7"
qsx --config qsx.toml context-build task-001 /new/context-bundle
qsx --config qsx.toml context-check task-001 /new/context-bundle
qsx --config qsx.toml handoff task-001 "Continue at lemma 4" --output /new/handoff
qsx --config qsx.toml history-reconcile
```

The controller parses and stores data and hashes bytes. It does not execute imported project code, hooks, tests, commands embedded in notes, or evidence programs.
