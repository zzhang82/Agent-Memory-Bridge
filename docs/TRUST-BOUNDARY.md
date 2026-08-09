# Trust Boundary

Agent Memory Bridge is a local-first MCP server for reusable engineering memory
and lightweight coordination. It is built for trusted local operators who want
inspectable memory, not for hosted identity, multi-tenant authorization, or
remote access control.

## What AMB Provides

- a local SQLite-backed memory and signal store
- explicit namespaces, tags, provenance fields, and record metadata for
  filtering and review
- optional local operating profiles that tighten cooperative behavior
- raw export paths for inspection, backup, migration, and debugging
- bounded local command-provider controls for classifier and embedding helpers
- short-lived HMAC recall receipts for binding explicit memory-recall results to
  feedback
- append-only retrieval feedback records that remain shadow-only evidence
- append-only run events and outcomes, with rebuildable current-state projections
- schema-v9 recovery metadata: terminal timestamps, one-snapshot run reads, and
  projection-health reporting

## What AMB Does Not Provide

- authenticated client, user, model, or workspace identity
- OAuth, login, hosted identity, or per-namespace ACL enforcement
- multi-user or remote ACL systems
- sandboxing for configured local command providers
- distributed locking or exactly-once coordination across machines
- encrypted recall receipt tokens; they are tamper-evident, not confidential
- ranking, memory mutation, or policy changes from retrieval feedback
- ranking, policy, prompt, or memory promotion changes from run outcomes or
  memory attribution
- durable raw chain-of-thought, transcript archives, or artifact binaries in the
  run ledger
- compliance certification or regulated-data handling guarantees

## Local Operating Profiles

`local-single-user` is the compatibility profile for one trusted local operator.
`hardened-local` requires Signal claim-before-ack behavior and rejects trusted
shell command providers.

Both are cooperative local governance profiles. They help a disciplined local
operator avoid accidental misuse, but they do not authenticate callers, isolate
users, enforce namespace permissions, sandbox helper commands, or protect a live
database shared across remote machines.

## Declared Provenance And Tags

Fields such as `source_client`, `source_model`, `client_session_id`,
`client_workspace`, and `client_transport` are declared provenance. They may come
from a tool call or from local launcher defaults.

AMB stores those values so records are easier to filter, debug, compare, and
audit. They are not proof that a specific client, model, user, workspace, or
vendor produced the record.

Recall receipts and feedback responses use the same boundary. Their provenance
fields are server- or caller-declared metadata, not authenticated origin.

Caller-supplied tags are also declared labels. They improve retrieval and review,
but they are not authenticated authority. Security-sensitive or governance
behavior must validate policy at the bridge boundary rather than trusting a tag
because a caller supplied it.

## Recall Receipts

Durable memory text recall can return a 15-minute `recall_receipt` for
`kind = "memory"` queries. The token is signed with a local HMAC secret so AMB
can reject tampering, expired receipts, namespace mismatches, bridge-instance
mismatches, database-epoch mismatches, and memory-id/rank mismatches.

The token is not encrypted. Treat it as sensitive local metadata because it can
carry namespace, retrieval-mode, database-epoch, result-id/rank, and hashed-query
details. The receipt does not authenticate the caller, client, model, workspace,
or user.

The database epoch in a receipt is a restore-instance guard. It helps reject
receipts minted before a restore or epoch rotation, but it is not per-write
freshness and does not prove that no later write changed the database.

## Retrieval Feedback

The `feedback` MCP tool records structured outcomes such as `helpful`,
`misleading`, `outdated`, `not_applicable`, and `not_used` for one recalled
memory result. It is append-only, receipt-bound, and shadow-only.

Feedback does not rewrite memories, rebuild indexes, promote or suppress
records, change recall results, or train ranking behavior. Use it as audit and
review evidence, not as an automatic policy or learning path.

## Run And Episode Evidence

Schema v9 preserves the v8 durable run declarations, work items, structured
events, outcome chains, artifact references, and receipt-bound memory links,
while adding `terminal_at` and `current_outcome_updated_at` through an
authority-preserving migration. Event and outcome rows are append-only. Current
run and work-item state is a materialized projection that can be checked and
rebuilt from those authority rows.

Run, event, outcome, artifact, and link rows are durable episode authority;
projections and downstream learning/consolidation effects are shadow-only.

An artifact row has a server-minted artifact ID linked to caller-declared,
validated digest, MIME, URI, and reference metadata alongside its producing
event. Artifact metadata rejects inline-body keys `body`, `file_body`, and
`fileBody` recursively, alongside the existing content/binary keys. Ingestion
also rejects hidden-reasoning field names recursively, including normalized
case/separator variants, before data reaches SQLite. `complete_run` never
coerces unfinished work items: the first outcome requires each explicit item to
already be terminal. The work-item FSM rejects terminal reopen attempts, and
event callers may provide expected sequence/status compare-and-swap
preconditions. `get_run` uses one SQLite read transaction and returns
`snapshot_epoch`, `snapshot_last_sequence`, `projection_health`, and
`degraded`; mutating run writes fail closed while projection health is degraded.
Outcome correction preserves the original `terminal_at` while updating
`current_outcome_updated_at`.

Legacy v1 `verified_success` outcomes remain readable as `legacy_declared`, but
they are not strong verification and cannot authorize regression targets,
consolidation support, or utility supporting-run credit. Governed-v2 receipts
are deferred to 0.27.2. Utility and consolidation remain shadow-only.

The durable episode rows do not change recall ordering, promote memory, edit
policy, rewrite prompts, or train a model. AMB rejects receipt-shaped values
before durable SQLite persistence and does not echo them in validation errors.
External client and process logging are outside this guarantee. Caller-provided
agent, thread, workspace, evaluator, and provenance labels remain declared
metadata.

## Exports Are Sensitive

Exports are readable snapshots of stored records and metadata. They can include
raw memory content, namespaces, tags, timestamps, local paths, sessions,
workspace labels, model labels, and caller-declared provenance.

Treat exports as sensitive project memory. Sanitize or replace private values
before sharing them in public issues, discussions, pull requests, reports, or
benchmarks.

## Contributor Rule

Public docs and examples should preserve this boundary. Do not describe AMB as
an identity system, ACL system, hosted memory platform, OAuth integration,
compliance layer, or remote multi-user backend unless that work is explicitly
scoped and implemented.
