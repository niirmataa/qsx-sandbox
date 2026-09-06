# Research Memory RM01–RM08 closure

## Published-revision verification rerun (2026-09-06)

GitHub PR #1 was reported by the operator at `5752d5f14d4a41dac027b6c930be3834eec530f7`.
The platform materialized the same submitted change set locally as commit
`bfd63dc25f934771c27a77e2fd56167472c42c4e`, tree
`a929d79befe81594b66e7c40240c6dc166c9b46e`. The published commit object is not
present in this checkout, no remote is configured, and an unauthenticated GitHub
read returned HTTP 401. Consequently commit/tree equality with the published SHA
was **NOT ESTABLISHED**; the gates below were rerun against the stated local tree.
No F01–F03 implementation was repeated in this verification pass.

Tested code revision: `419e42f10e6924fb016c705a1d03db0c4107fbeb` (tree `0db6111ac6aff4ea2fc7a9fa3563630ca30c39cd`).
The final documentation commit is intentionally later; its HEAD is reported in the delivery message rather than recursively embedded here. Evidence layer is Codex Cloud controller integration with synthetic files, real SQLite/Git and offline qemu-img where applicable—not a real VM acceptance.

| RM | Implementation | Collected pytest identifiers | Execution | Limitations |
|---|---|---|---|---|
| RM01 | Export copies immutable generation including every `events/<id>.json`; semantic equality to task-filtered JSONL is checked | `test_export_contains_complete_linked_events_and_detects_damage` | PASS | Physical transfer NOT RUN |
| RM02 | Exact file inventory plus separately reported evidence completeness; event hashes and task revision are recomputed from package bytes | `test_export_contains_complete_linked_events_and_detects_damage`, `test_context_bundle_staleness_and_project_scoped_artifact_export`, `test_missing_and_corrupt_required_evidence_are_not_ready`, `test_exported_record_hash_and_revision_are_recomputed` | PASS | Authenticity remains NOT_ESTABLISHED |
| RM03 | Task revision hashes task event IDs/hashes; global read point is separate and informational | `test_task_revision_ignores_unrelated_task_records` | PASS | Shared active-state changes intentionally affect freshness |
| RM04 | Idempotency scope includes project/task/channel/local ID and immutable request hash; disk operations bind target/parameters before mutation | `test_full_record_lookup_idempotency_and_correction`, `test_same_local_operation_id_is_scoped_by_task`, `test_guest_outbox_collection_is_idempotent_and_cannot_claim_authority`, `test_retry_after_render_failure_republishes_without_duplicate`, `test_rollback_operation_id_conflict_is_before_disk_change` | PASS | Physical crash/power-loss NOT RUN |
| RM05 | Applicability and correction relationships remain independent; guest outbox preserves validated same-task `supersedes` links through export | `test_context_budget_includes_footer_and_correction_unit`, `test_applicability_distinguishes_general_historical_and_unknown`, `test_guest_outbox_correction_survives_complete_export_path` | PASS | Conflicting corrections remain visible, not automatically adjudicated |
| RM06 | Nonblank checker/version, string types, complete lowercase SHA-256 input/output; guest channel cannot self-promote | `test_independent_verification_requires_real_metadata`, `test_independent_verification_rejects_incomplete_values`, `test_guest_cannot_promote_its_pass_to_independent` | PASS | Metadata validation does not prove checker execution |
| RM07 | Entire UTF-8 output including required header/warnings/footer is bounded; no record/correction unit splitting | `test_context_budget_reports_omissions`, `test_context_budget_includes_footer_and_correction_unit`, `test_empty_context_budget_boundary_is_measured_in_utf8_bytes` | PASS | Byte budget is not a token estimate |
| RM08 | Immutable generation directories and atomic symlink pointer; export copies one captured generation | `test_generation_pointer_survives_interrupted_publication`, `test_interruption_after_pointer_keeps_complete_new_generation`, `test_parallel_export_is_one_complete_historical_generation`, `test_parallel_writes_and_recovery_are_explicit` | PASS | Injected process errors are not physical power-loss tests; SQLite/QCOW2 are not one transaction |

## Gate results

- PASS: `make test-cloud` — 54 tests.
- PASS: `make lint`.
- PASS: `make typecheck`.
- PASS: `make security-audit`.
- PASS: `make install-wheel`.
- PASS: `git diff --check`.
- BLOCKED: real KVM boot, stopped real-guest collection, VM rollback validation, libvirt/network confinement.
- NOT RUN: physical power interruption, separate physical-media recovery, signed independent trust anchor.
