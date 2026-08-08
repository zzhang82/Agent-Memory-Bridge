# Closed-Loop Episode Contract

This document defines the current development contract for explicit run state in
Agent Memory Bridge. It extends the published MCP interoperability baseline
without adding MCP Tasks, implicit connection state, automatic ranking changes,
automatic policy changes, raw chain-of-thought storage, or automatic lesson
promotion.

## Public MCP Surface

The canonical public MCP tool order is:

1. `store`
2. `recall`
3. `browse`
4. `stats`
5. `forget`
6. `feedback`
7. `promote`
8. `annotate`
9. `revise`
10. `export`
11. `begin_run`
12. `record_run_event`
13. `get_run`
14. `complete_run`
15. `claim_signal`
16. `extend_signal_lease`
17. `ack_signal`

The first ten memory/change tools and the final three Signal tools retain their
published semantics. The four run tools are additive. Runtime registration,
schema digest, raw-wire proofs, SDK proofs, operator probes, README facts, and
release checks must agree with `mcp_boundary.PUBLIC_TOOL_ORDER`.

## Stateless Run Handles

`begin_run` mints a high-entropy `run_id` and root `work_item_id`. The server
does not retain an implicit current run or current work item. Every later call
must provide the declared `workspace_key` and the server-minted handle. A single
connection may interleave multiple runs, and reconnect recovery requires only
the workspace and run ID.

`work_item_id` identifies an item in the agent's business work tree. It is not a
coordination `signal_id` and is not a future MCP Tasks `taskId`.

## Event And Outcome Authority

Run, event, outcome, artifact, and link rows are durable episode authority;
projections and downstream learning/consolidation effects are shadow-only.

`record_run_event` appends a strictly validated version-1 event. Per-run sequence
allocation, optional child work-item creation, authority insertion, and derived
projection updates occur in one transaction. Event payload and evidence fields
are bounded to 32 KiB, and raw transcript or hidden-reasoning fields are rejected.
This recursive policy also rejects normalized field-name variants such as
`Thought-Process` or `ANALYSIS`; factual digest fields such as `analysis_digest`
remain permitted.

`artifact_created` requires `payload.artifact` with caller-declared, validated
lowercase SHA-256 digest, MIME type, URI, and optional bounded object metadata.
The server mints the artifact ID and links it to that metadata in the same
transaction. Metadata rejects inline-body keys `body`, `file_body`, and
`fileBody` recursively, alongside the existing content/binary keys. The event
response includes that artifact reference, and `get_run` returns only artifact
references produced by the events in its page.

`get_run` is read-only. It returns current run/work-item projections, ordered
events after `since_sequence`, and the current append-only outcome head.

`complete_run` appends an outcome or an explicit superseding correction.
`verified_success` requires non-empty deterministic-verifier or human evidence;
agent self-report alone remains `unverified`. A `regression` must name a distinct
same-workspace target whose current outcome head is `verified_success`. Durable
episode authority does not change memory ranking, policy, prompts, or durable
procedures by itself.

Before the first outcome, every root and child work-item projection must already
be terminal through explicit ledger events: `work_item_completed`,
`work_item_failed`, or `work_item_abandoned`. The server does not synthesize
terminal events. A later correction supersedes a valid current outcome head.

## Receipt-Bound Memory Attribution

This receipt-bound memory attribution contract ties episode evidence to the
exact signed exposures that produced it.

`memory_recalled`, `memory_applied`, and `memory_rejected` require a typed
`memory_attribution`; other event types reject it. A recalled event supplies a
namespace, signed recall receipt, and one to 32 selected memory/rank pairs. A
zero-item `memory_recalled` is allowed only when that signed receipt has zero
results and zero exposures; it still validates namespace, signature, epoch, and
receipt contract, but creates no synthetic memory link. The server validates the
receipt once, then validates every selected signed exposure, database epoch,
namespace, and current exact content version in the event transaction. It records
only the receipt hash, exact version, and rank in `run_memory_links`. AMB rejects
receipt-shaped values before durable SQLite persistence and does not echo them in
validation errors. External client and process logging are outside this guarantee.

Applied and rejected events either select explicit links from an earlier
same-run `memory_recalled` event, or use a manual `{memory_id,
exact_content_version}` reference. Source-linked attribution may include only a
current effective feedback head that matches the original receipt hash, namespace,
memory, rank, and exact version. Manual attribution has no receipt, rank, or
feedback link and is always `review_required`; it is excluded from utility
credit. Callers cannot set relation, receipt hash, review state, or outcome ID.

`memory_utility_shadow` is a rebuildable, score-zero projection. It counts only
non-review links whose feedback ID is still the effective head and whose run has
a current outcome head. `supporting_run_count` is deliberately conservative:
distinct runs with helpful feedback and `verified_success`; `contradicting_run_count`
uses misleading or outdated feedback with failed, user-corrected, or regression
outcomes. These counters are review evidence only and never affect recall order,
memory authority, promotion, or policy.

## Idempotency And Trust Boundary

Mutating run calls require caller-generated idempotency keys. Only SHA-256
digests of those keys are stored. Identical retries return the original
server-minted record; reuse with a different canonical request payload fails.

Workspace, agent, thread, client, model, harness, and evaluator labels remain
caller-declared provenance rather than authenticated identity. MCP metadata is
bounded at the transport boundary and cannot grant authorization or bypass
durable validation.

## Compatibility And Non-Goals

The dual-era stdio cache contract remains unchanged: `server/discover` is
`300000/public`, while `tools/list` is `0/private`. Modern and legacy clients
receive the same deterministic 17-tool surface. Existing memory, Signal,
feedback, receipt, and retrieval behavior must remain compatible.

This contract explicitly excludes MCP Tasks, hosted execution, automatic
reranking, automatic prompt or policy modification, raw chain-of-thought
persistence, online training, and automatic lesson promotion.
