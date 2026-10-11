# Production Status

This page is the canonical reference for **checked-in current-source facts**: implementation version, public surface, capability boundaries, and validated source evidence. It deliberately does not snapshot the moving `main` commit, latest live CI result, latest published GitHub Release, or live PyPI availability. Consult [GitHub Actions](https://github.com/zzhang82/Agent-Memory-Bridge/actions), [GitHub Releases](https://github.com/zzhang82/Agent-Memory-Bridge/releases), and PyPI for those live repository and distribution states. Historical release announcements and proof artifacts remain evidence, but they are not the current-source status record.

## Current Source Status

| Field | Current fact |
|---|---|
| Package/source version | `0.36.0` |
| Durable schema | v12 |
| Public MCP surface | Exactly 17 public MCP tools |
| Public tool-schema digest | `24c5c52321d61b4b6f647c0d74e2d8304ca68716c403e08a274e9badfd8dc9f8` |
| Runtime model | Default local stdio and optional standard Streamable HTTP over one host-local SQLite/WAL authority; FTS5 and optional embeddings are derived indexes |

Current source test collection: `1253 tests`

Default local home is `~/.local/share/agent-memory-bridge`. Current source does not discover `~/.codex/mem-bridge`. Set `AGENT_MEMORY_BRIDGE_HOME` to keep the old directory, or copy it to the neutral home. AMB does not migrate or delete it.

> A tag is not a GitHub Release, and live CI or package-index state is not host certification, a distribution guarantee, or a productivity result. Installation guidance retains explicit publication and source-checkout gates.

## Distribution Status

The `0.32.1` source line introduced the release-side contract for PyPI distribution. Current source `0.36.0` uses the same publication route: a published GitHub Release whose tag matches `v<project.version>` can build and verify distributions, then publish through PyPI Trusted Publishing with GitHub OIDC. No PyPI API token is stored in the repository workflow. The container workflow builds and tests the immutable release-event SHA before GHCR publication. Live package/image availability is external state and is not asserted by this checked-in document.

The `0.36.0` source line removes named-client harness adoption from AMB Core. It does not ship Codex or OpenCode adapters, the `setup` and `config` commands, or automatic discovery of `~/.codex/mem-bridge`. Schema remains v12 and the public MCP surface remains exactly 17 tools. Existing data is not migrated or deleted. Production NAS cutover remains a separate owner-approved operation.

The historical `0.35.0` source line packaged Governed Lifecycle Activation: resolver-owned scope, optional host recall, bounded Stop review-candidate capture, configured remote recall authority, and a frozen live measurement contract. The [lifecycle release contract](evidence/v0.35.0-lifecycle/CONTRACT.md) binds completed evidence and its tested boundaries. The release identity/documentation changes do not add runtime behavior, schema, public tools, or automatic durable promotion.

The `0.34.1` release is a portability/reliability patch on that remote-authority line. It keeps filesystem classification fail-closed on Windows and symlink-resolution loops, without changing durable schema v12, the 17-tool public MCP surface, or the no-automatic-learning boundary.

The `0.34.0` release adds an optional official Streamable HTTP deployment surface, local-filesystem deployment guards, HTTP-aware doctor/verify, and a gated container publication path. It keeps durable schema v12, exactly 17 public MCP tools, and the no-automatic-learning boundary. Live package/image availability and production NAS cutover remain external, owner-gated operations. The [frozen deployment contract](evidence/v0.34.0-remote-authority/CONTRACT.md) records the evaluation obligations for this line.

The `0.33.0` release operationalizes the existing product boundary: fresh-session first-win evidence, MCP 2.x floor/latest compatibility gates, cross-platform process-liveness validation, clearer public error semantics, and a documented single-authority multi-machine topology. It keeps durable schema v12, exactly 17 public MCP tools, and the no-automatic-learning boundary.

## Historical Tag Reference

The `v0.27.4` tag identifies the historical source snapshot `e8210cb204e501650a59876502a2028c7aae9afe`. This is a stable tag fact, not a claim about the checked-out `main` commit, current publication state, or live CI status.

The `v0.28.0` tag identifies the historical release merge snapshot `c6e3568a59852c5b589d6aba00b89ab580c228e6`. This is a stable historical release fact, not a claim about the current source head or current publication state.

## Implemented Capability Summary

### Optional host lifecycle activation

Current source includes an optional host hook, `lifecycle-hook`. It resolves the checkout through `project resolve` and may recall once from the selected local SQLite or configured Streamable HTTP authority. It is not loaded by plain MCP use, does not add a public tool, and does not write ordinary durable memory. An exact repeated prompt is suppressed; a reworded prompt can recall again. `AGENT_MEMORY_BRIDGE_AUTHORITY_URL` or `[deployment].authority_url` selects remote recall without opening or falling back to local SQLite. HTTP failures produce `error`, separately from successful `no_hit`; skipped events do not contact the authority. A missing local database is `unknown`, not an empty recall and not a claim that AMB is down. The old Codex Stop trial is historical: it used the historical host event shape and does not constitute acceptance of the current generic lifecycle-hook input, nor does it claim a new host trial.

The historical `plain_mcp_baseline` / `known-project-gotcha` FAIL remains separate from adapter evidence. The [2026-10-03 Codex live evaluation](../benchmark/lifecycle-activation-live-evaluation.md) completed all ten frozen v2 cases with invocation-inline hooks and an isolated loopback HTTP authority: 3 PASS, 7 FAIL, 0 INCONCLUSIVE. In that historical pre-repair run, required-recall coverage was 1/5 and false activation was 0/2. The opt-in `codex-repo-read-v1` measurement revision recognizes the stale-conflict trace's `rg` read and removes only its false `repo_evidence_missing` reason; the full-cohort re-score preserves the original verdicts and unchanged decision grader. The [sanitized evidence anchor](../benchmark/lifecycle-activation-codex-2026-10-03.anchor.json) preserves the full collection identity, all 40 private input digests and paired results in Git; re-scoring can pin those inputs with `--expected-anchor`. #50's measurement contract merged and closed with this honest product-failure baseline retained. Remote lifecycle HTTP integration tests alone are not live-host benchmark acceptance.

The #51 repair uses bounded rule-based activation, not general natural-language history detection. Its repaired Codex collector lived at `scripts/run_codex_lifecycle_activation_benchmark.py` on tag `v0.35.0`. Current Core does not ship that collector, render a Codex config, or certify a host. The original #50 measurement instrument remains byte-for-byte at `scripts/run_lifecycle_activation_benchmark.py` and `tools/evidence/lifecycle_activation.py` so the published result can still be checked. That instrument is historical evidence, not a current install path or CI gate. The earlier Sol/local/project-hook 10/10 remains historical supporting evidence on its own source bytes; it does not establish the matched Luna result or issue closure.

The [matched #51 evidence](evidence/issue-51-codex-matched/README.md) on repaired source `483c755e7299b96990b0a75f4363027c21138c47` records 10 PASS / 0 FAIL under Codex CLI 0.160.0 / `gpt-6-luna` / `adapter_enabled` / `remote_loopback` / `invocation_inline` / frozen v2. It uses the same `codex-repo-read-v1`, expected decisions, grader and timeout/isolation contract as the historical baseline. Required recall is 5/5, false activation 0/2, actual no-hit 1/1, unavailable discrimination 1/1, stale misuse 0 and unnecessary recall 0. Source-bound receipts preserve native callbacks, live stale reconciliation and unchanged durable-memory rows. The intermediate 8 PASS / 2 FAIL is retained; historical values absent from repository files are distinguished from independently verified current-state claims.

#51 merged as `db9923508cde457c4b3181d8861a4785fd5cc065` after exact-head review and 18 passing CI/package checks. [Canonical post-merge verification](https://github.com/zzhang82/Agent-Memory-Bridge/issues/51#issuecomment-5972120034) checked all 99 source hashes plus report/receipt identities and reproduced the published 10/10 report with the canonical grader and retained observations, without new model calls. #50 and #51 are closed/completed, and the canonical merge's [main CI](https://github.com/zzhang82/Agent-Memory-Bridge/actions/runs/37143745256) passed. Those product/canonical gates are complete; release-PR validation, release-merge tag binding, PyPI/GHCR publication and clean-install verification remain separate distribution gates.

### v0.34 remote authority deployment

V0.34 productizes optional remote deployment in the official package without expanding durable memory authority. Local stdio remains the default. The same 17-tool MCP server can serve standard Streamable HTTP. One host keeps SQLite/WAL on a verified local filesystem; remote clients call that authority instead of opening a network-mounted database. Official Docker/Compose artifacts use a non-root process, a host-local volume, and a readiness check that does not expose memory contents. `doctor`/`verify` can probe HTTP reachability without creating a local fallback writer. Production replacement of an existing NAS authority remains a separate owner-approved operation.

### v0.33 operationalization

V0.33 makes the existing AMB product easier to validate and operate without expanding its durable authority. The primary first-win proof is now a decision stored through one stdio process and recalled from a fresh stdio process against the same isolated home. The Python MCP SDK policy is `mcp>=2.0.0,<3`; floor and latest supported 2.x compatibility jobs feed the stable `CI success` aggregate gate. Windows process-liveness checks use `OpenProcess(SYNCHRONIZE)` plus `WaitForSingleObject(handle, 0)` rather than treating an open handle as liveness. Expected public validation failures remain model-correctable while unexpected internal `ValueError`s are kept off the public `ToolError` path. Multi-machine guidance keeps one canonical SQLite/WAL host and reaches it through an adapter rather than a network-shared WAL database; committed-but-unacknowledged writes are explicitly possible when a response is lost after commit.

### Governed Project Memory positioning and onboarding

V0.32.2 makes the existing product boundary easier to understand without changing runtime authority. Public positioning now describes AMB as governed project memory that carries useful context across sessions, tools, and time. The README and hero show fragmented project context converging into governed memory and being reused by future coding-agent sessions. Installation is explicitly separate from MCP client registration: each client must be connected, safe setup remains preview-first, and clients that should share memory must use the same persistent `AGENT_MEMORY_BRIDGE_HOME`.

### Project Knowledge Activation

V0.30 adds a persistent, bounded, rebuildable derived repository snapshot. Explicit namespace binding and canonical local Git roots isolate independent clones and worktrees; a clean HEAD is required for commit-bound eligibility, while dirty, stale, moved, or unavailable sources fail closed. The existing MCP `recall` response keeps selected repository WHAT in `repository_knowledge` and governed durable WHY in normal `items`; repository facts are never promoted into durable memory.

### Project Learning UX

V0.32.0 introduced the current Project Learning UX. Default Explore presents repository CODE / WHAT separately from governed CONVERSATION / WHY. `project init` is the preferred first-project path: it detects a local Git checkout, proposes a namespace, requires explicit confirmation, revalidates after confirmation, bootstraps derived repository WHAT, and shows Human-first Explore. Repeat init refreshes repository WHAT and leaves existing project WHY unchanged. This is not automatic learning and does not add an MCP tool.

Current source can also read that binding back. `project resolve [path]` returns the governed namespace for the current worktree when exactly one binding matches. It writes nothing: an unbound checkout stays `no_binding`, and conflicting or unreadable bindings fail closed as `ambiguous_binding`. A dirty or stale checkout can still be identified, while stored repository WHAT stays ineligible. Windows local origin paths that differ only by slash, drive-letter case, or a trailing `.git` share one logical remote identity; distinct local clones and linked worktrees stay distinct. This command is not an MCP tool.

### Project WHY Alignment

V0.31.1 made explicit project decisions and constraints first-class governed task-memory lanes. The same active WHY that recall can see reaches Task Memory, Inspect, the Context Compiler, and Knowledge Explorer. Revision, supersession, validity, lineage, and structured-metadata governance remain in force.

### Knowledge Explorer

V0.31 implements a local, read-only, bounded, deterministic, namespace-bound, rebuildable, provenance-bearing projection over existing project knowledge. Repository WHAT remains `derived_repository`; governed durable decision/constraint WHY remains `governed_durable_memory`; the namespace connector is `derived_projection`. Stale repository snapshots fail closed. The primary durable-memory raw scan is bounded at 500 rows, and relation targets use at most 100 unique IDs through direct same-namespace query-only lookup. Missing, existing-but-ineligible, and budget-exhausted targets are distinct diagnostics. Explorer adds no ranking, learning, writeback, graph database, or new MCP tool.

### Durable memory and governed recall

AMB stores local engineering memory, revisions, lifecycle state, relations, and coordination signals. Lifecycle-aware recall and governed task-memory assembly suppress ineligible, stale, superseded, unsafe, or otherwise governed-out guidance before it becomes task context. Memory records remain correctable through existing governed mutation paths.

### Dynamic State authority

Dynamic State is an internal exact-key release-state authority lane. It provides typed status transition, owner assignment, and restore commands; optimistic version and database-epoch guards; lifecycle idempotency; immutable accepted mutation and terminal request-outcome history; and a deterministically rebuildable state-head projection.

Dynamic State is separate from semantic memory, retrieval, ranking, embeddings, FTS, ordinary memory writes, and the MCP public tool surface.

### Context and episode evidence

The Context Compiler is a transient derived-view layer. It accepts explicit bounded Repository Knowledge / WHAT with `derived_repository` authority, relation-aware governed task memory, exact Dynamic State read snapshots, and explicit session-local items. It does not query storage, rerun recall, rank records, persist a manifest, or add an MCP tool. Repository WHAT remains separate from durable WHY, and rendered context remains in process; manifest serialization retains metadata and digests rather than prompt-facing bodies.

A bounded context attestation can be recorded through the existing episode artifact path. It stores metadata and digests, not raw task text, rendered context, memory bodies, or session bodies. Read-only evaluation linkage can report whether valid context-attestation evidence is bound to the current strong verified outcome. It does not claim that selected context caused an outcome.

Run, event, outcome, artifact, and link rows are **durable episode authority**; projections and **downstream learning/consolidation effects are shadow-only**. Strong verification continues to depend on the existing governed episode and verification-receipt authority.

## Runtime and Data Boundaries

| Boundary | Current behavior |
|---|---|
| Durable authority | SQLite/WAL holds memory, state, run, work-item, event, artifact, outcome, and receipt authority under their existing contracts. |
| Derived views | Repository knowledge snapshots, FTS5, optional embedding sidecars, task-memory assembly, projections, reports, and evaluation linkage are rebuildable and non-authoritative. |
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

The v0.19 and v0.20 rows below are frozen historical report facts. Current source no longer runs those proof commands.

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
