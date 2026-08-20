# Production Status

This page is the canonical reference for **checked-in current-source facts**: implementation version, public surface, capability boundaries, and validated source evidence. It deliberately does not snapshot the moving `main` commit, latest live CI result, or latest published GitHub Release. Consult [GitHub Actions](https://github.com/zzhang82/Agent-Memory-Bridge/actions) and [GitHub Releases](https://github.com/zzhang82/Agent-Memory-Bridge/releases) for those live repository states. Historical release announcements and proof artifacts remain evidence, but they are not the current-source status record.

## Current Source Status

| Field | Current fact |
|---|---|
| Package/source version | `0.27.4` |
| Durable schema | v12 |
| Public MCP surface | Exactly 17 public MCP tools |
| Public tool-schema digest | `24c5c52321d61b4b6f647c0d74e2d8304ca68716c403e08a274e9badfd8dc9f8` |
| Runtime model | Local stdio MCP over SQLite/WAL; FTS5 and optional local embeddings are derived indexes |

Current source test collection: `920 tests`

> A tag is not a GitHub Release, and live CI state is not host certification, a distribution claim, or a productivity result. Installation guidance retains its own pinned-release and source-checkout gates.

## Historical Tag Reference

The `v0.27.4` tag identifies the historical source snapshot `e8210cb204e501650a59876502a2028c7aae9afe`. This is a stable tag fact, not a claim about the checked-out `main` commit, current publication state, or live CI status.

## Implemented Capability Summary

### Durable memory and governed recall

AMB stores local engineering memory, revisions, lifecycle state, relations, and coordination signals. Lifecycle-aware recall and governed task-memory assembly suppress ineligible, stale, superseded, unsafe, or otherwise governed-out guidance before it becomes task context. Memory records remain correctable through existing governed mutation paths.

### Dynamic State authority

Dynamic State is an internal exact-key release-state authority lane. It provides typed status transition, owner assignment, and restore commands; optimistic version and database-epoch guards; lifecycle idempotency; immutable accepted mutation and terminal request-outcome history; and a deterministically rebuildable state-head projection.

Dynamic State is separate from semantic memory, retrieval, ranking, embeddings, FTS, ordinary memory writes, and the MCP public tool surface.

### Context and episode evidence

The Context Compiler is a transient derived-view layer. It consumes relation-aware governed task memory, exact Dynamic State read snapshots, and explicit session-local items. It does not query storage, rerun recall, rank records, persist a manifest, or add an MCP tool. Rendered context remains in process; manifest serialization retains metadata and digests rather than prompt-facing bodies.

A bounded context attestation can be recorded through the existing episode artifact path. It stores metadata and digests, not raw task text, rendered context, memory bodies, or session bodies. Read-only evaluation linkage can report whether valid context-attestation evidence is bound to the current strong verified outcome. It does not claim that selected context caused an outcome.

Run, event, outcome, artifact, and link rows are **durable episode authority**; projections and **downstream learning/consolidation effects are shadow-only**. Strong verification continues to depend on the existing governed episode and verification-receipt authority.

## Runtime and Data Boundaries

| Boundary | Current behavior |
|---|---|
| Durable authority | SQLite/WAL holds memory, state, run, work-item, event, artifact, outcome, and receipt authority under their existing contracts. |
| Derived views | FTS5, optional embedding sidecars, task-memory assembly, projections, reports, and evaluation linkage are rebuildable and non-authoritative. |
| Context persistence | Compiled and rendered context bodies are transient. An attestation is metadata-only and is not a prompt archive. |
| Artifact privacy | Episode artifact metadata rejects inline body fields including `body`, `file_body`, and `fileBody`; receipt-shaped values are rejected before durable persistence. |
| Memory improvement | Feedback, utility, consolidation, and candidate evidence do not automatically change ranking, policy, prompts, durable memory, or procedures. |
| Public interface | The existing 17-tool MCP surface remains the public boundary; Context Compiler and Dynamic State are internal layers. |

## Validation Status

The checked-in validation contracts verify the source facts on this page. For the current remote workflow state, use [GitHub Actions](https://github.com/zzhang82/Agent-Memory-Bridge/actions); the repository also checks formatting and linting in its normal validation path.

<details>
<summary>Current source facts validated against checked-in snapshot reports</summary>

```text
question_count = 11
memory_expected_top1_accuracy = 1.0
memory_mrr = 1.0
file_scan_expected_top1_accuracy = 0.636
file_scan_mrr = 0.909

sample_count = 16
classifier_exact_match_rate = 0.875
fallback_exact_match_rate = 0.062
classifier_better_count = 13
fallback_better_count = 2
classifier_filtered_low_confidence_count = 2

case_count = 7
flat_case_pass_rate = 0.429
governed_case_pass_rate = 1.0
flat_blocked_procedure_leak_rate = 1.0
governed_blocked_procedure_leak_rate = 0.0
governed_governance_field_completeness = 1.0

signal_contention_case_count = 5
signal_contention_case_pass_rate = 1.0
unique_active_claim_rate = 1.0
duplicate_active_claim_count = 0
active_reclaim_block_rate = 1.0
stale_ack_blocked_rate = 1.0
stale_reclaim_success_rate = 1.0
pending_under_pressure_claim_rate = 1.0
initial_hard_expiry_cap_rate = 1.0

adversarial_case_count = 6
adversarial_task_count = 7
adversarial_governed_task_pass_rate = 1.0
adversarial_governed_blocked_record_leak_rate = 0.0

memory_evolution_case_count = 6
memory_evolution_task_count = 7
memory_evolution_governed_task_pass_rate = 1.0
memory_evolution_governed_blocked_record_leak_rate = 0.0
memory_evolution_governed_disposition_reason_hit_rate = 1.0

review_queue_item_count = 6
review_queue_actionable_count = 6
review_queue_hidden_lane_count = 2
review_queue_writeback_plan_count = 6
review_queue_no_auto_mutation = true
review_queue_public_mcp_surface_change = false
review_queue_item_type_count = 6

review_workflow_source_queue_item_count = 6
review_workflow_item_count = 6
review_workflow_manual_step_count = 27
review_workflow_requires_human_count = 6
review_workflow_auto_write_count = 0
review_workflow_no_auto_writeback = true
review_workflow_public_mcp_surface_change = false
review_workflow_item_type_count = 6

task_brief_used_count = 2
task_brief_ignored_count = 1
task_brief_needs_review_count = 4
task_brief_review_queue_item_count = 2
task_brief_active_signal_count = 1
task_brief_no_auto_writeback = true
task_brief_public_mcp_surface_change = false
task_brief_needs_review_source_type_count = 3

v019_case_count = 12
v019_pass_count = 12
v019_pass_rate = 1.0
v019_retrieval_case_count = 4
v019_retrieval_pass_rate = 1.0
v019_task_brief_case_count = 4
v019_task_brief_pass_rate = 1.0
v019_first_run_adoption_case_count = 4
v019_first_run_adoption_pass_rate = 1.0
v019_public_mcp_tool_count = 10
v019_public_mcp_surface_change = false
v019_client_config_write_count = 0
v019_durable_writeback_count = 0
v019_amh_required = false
v019_native_memory_comparison_required = true

v020_case_count = 6
v020_pass_count = 6
v020_pass_rate = 1.0
v020_import_sanity_pass = true
v020_stdio_round_trip_pass = true
v020_first_run_pass = true
v020_task_brief_pass = true
v020_public_mcp_tool_count = 10
v020_public_mcp_surface_change = false
v020_client_config_write_count = 0
v020_explicit_demo_memory_write_count = 1
v020_explicit_demo_signal_write_count = 0
v020_non_demo_durable_writeback_count = 0
v020_amh_required = false
v020_external_vendor_adoption_claim = false

v021_case_count = 20
v021_category_count = 4
v021_flat_baseline_hazards = 17
v021_governed_case_pass_count = 20
v021_governed_failures = 0
v021_governed_checkpoint_passes = 40
v021_governed_checkpoint_result_count = 40
v021_useful_current_retention_pass = true
v021_suppress_all_can_pass = false
v021_public_mcp_tool_count = 10
v021_public_mcp_surface_change = false
v021_auto_writeback_count = 0
v021_config_write_count = 0
v021_durable_live_writeback_count = 0
```

</details>

## Known Boundaries and Non-Claims

AMB does not provide hosted execution, a scheduler, a queue, MCP Tasks, Apps, OAuth/ACL, authenticated actor identity, an HTTP product surface, an ANN/graph database, automatic reranking, automatic prompt or policy mutation, online training, automatic durable writeback, or autonomous skill acquisition.

Caller-declared client, session, model, harness, and evaluator labels are provenance metadata, not authenticated identity. Local benchmark and proof artifacts are implementation evidence, not a vendor certification, external adoption proof, or general productivity result.

## Historical Evidence and Detailed References

| Need | Reference |
|---|---|
| Current high-level architecture | [Architecture](ARCHITECTURE.md) |
| Durable versus derived authority | [Authority Contract](AUTHORITY-CONTRACT.md) |
| Trust, privacy, and non-goals | [Trust Boundary](TRUST-BOUNDARY.md) |
| Run, artifact, receipt, and outcome contract | [Closed-Loop Episode Authority](CLOSED-LOOP-EPISODE.md) |
| Task-time assembly detail | [Context Assembly](CONTEXT-ASSEMBLY.md) |
| Historical proof and benchmark material | [Benchmark README](../benchmark/README.md) and existing versioned announcements |
| Published releases | [GitHub Releases](https://github.com/zzhang82/Agent-Memory-Bridge/releases) |
