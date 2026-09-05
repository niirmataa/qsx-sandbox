# Research Memory v1 — requirements map

Implementation status is separate from execution status. `PASS` below means a test was actually run; `IMPLEMENTED` means code exists.

| Requirement | Current function | Test/evidence | Layer | Implementation / executed evidence |
|---|---|---|---|---|
| Stable project/environment/task/run/session/lineage identity | task metadata; schema-v2 records | legacy migration and event tests | Cloud SQLite/filesystem | IMPLEMENTED; PASS |
| Transactional append, single writer, schema and idempotency | SQLite `BEGIN IMMEDIATE`; operation uniqueness | concurrent/idempotent/recovery tests | Cloud SQLite | IMPLEMENTED; PASS |
| Full record by event ID and correction/supersession | `get`; event JSON links; `supersedes` | lookup/details/correction test | Cloud SQLite/filesystem | IMPLEMENTED; PASS |
| Explicit execution status, claim type, provenance and observed source | schema axes and channel-assigned provenance | guest escalation/verification metadata tests | Cloud unit | IMPLEMENTED; PASS |
| Verifiable Git/checkpoint/artifact references | commit/checkpoint/artifact verification and manifest hash | artifact good/tampered/missing coverage; checkpoint integration | Cloud Git/filesystem | PARTIAL; PASS for executed cases |
| Unambiguous versioned workspace manifest | canonical `qsx-workspace-manifest-v1` encoding | content/mode/.git-scope tests | Cloud filesystem | IMPLEMENTED; PASS |
| Chronology distinct from active checkpoint lineage | `checkpoint-state.json` | CP-A/B/rollback-A/C test | Cloud qemu-img/controller | IMPLEMENTED; PASS |
| Durable operation intent/result and reconcile | operations INTENT/terminal/reconcile; rollback integration | abandoned intent/render retry tests | Cloud SQLite/filesystem | PARTIAL; PASS for executed cases |
| Local chain consistency vs independent authenticity | `verify()` reports NOT_ESTABLISHED without anchor | no-anchor test | Cloud unit | IMPLEMENTED (unsigned external trust anchor remains MISSING) |
| Atomic revision-consistent generated views | owned directory generation and atomic swap | concurrent append/render tests | Cloud SQLite/filesystem | IMPLEMENTED; PASS |
| Context state manifest, staleness, 12 KiB budget | state manifest, check, deterministic budget | stale state/budget tests | Cloud filesystem | IMPLEMENTED; PASS |
| Handoff/resume based on observed guest state | stopped-disk observation and context rebuild | branch mismatch/unit adapter | Cloud adapter contract | PARTIAL; real guest BLOCKED |
| Guest outbox collection with LOCAL_PENDING semantics | `collect_outbox`; stopped-disk CLI adapter | duplicate/untrusted collection tests | Cloud unit/adapter contract | IMPLEMENTED; physical collection BLOCKED |
| Project-scoped export with research bundle and artifacts | `.qsx-export/research` task bundle | scoped bundle/artifact tests | Cloud filesystem | IMPLEMENTED; PASS |
| Migration without invented historical facts | idempotent MIGRATED/RECONSTRUCTED event | legacy task test | Cloud SQLite/filesystem | IMPLEMENTED; PASS |
| Physical VM collection/restore/isolation | documented acceptance | Ubuntu host acceptance | Physical host | MISSING; BLOCKED |

The closure work will retain JSONL as the portable representation while using standard-library SQLite for transactional controller state. Hashes prove local chain consistency only unless compared with an independently stored trust anchor; neither hashes nor signatures establish scientific truth.
