# Roadmap

Last updated: 2026-07-07 (America/New_York)

This maintainer note tracks the shipped ladder through `0.19.0`, including Task Brief reports, the limited first-run adoption helper, and the fixed 12-case v0.19 adoption-proof pack. Treat it as a maintainer planning document, not as the public release contract.

## Shipped Ladder

- `0.7 = governed learning layer`
- `0.8 = credible, structured memory`
- `0.9 = applicable, compositional memory`

The planned `0.8` work shipped as part of the direct `0.9.0` release rather than as a separate public tag.

## What 0.9.0 Established

### 0.7 Governed Learning Layer

- bundle-first startup and runtime overlays
- profile/control-layer architecture
- compression before promotion
- conservative `belief-candidate -> belief` ladder
- replay, review, observation, and cutover diagnostics
- boundary-aware contradiction handling

### 0.8 Credible, Structured Memory

- benchmarked retrieval with reviewed fixtures
- `precision@k`, `recall@k`, `MRR`, and `expected_top1_accuracy`
- relation-lite metadata:
  - `supports`
  - `contradicts`
  - `supersedes`
  - `depends_on`
  - `valid_from`
  - `valid_until`
- relation-lite surfaced through:
  - recall item serialization
  - export
  - stats
  - deterministic proof
  - benchmark summary
  - healthcheck smoke
- local metadata-only telemetry

### 0.9 Applicable, Compositional Memory

- `concept-note` as a first-class stored artifact derived from stable beliefs
- `procedure` as a retrieval-native durable pattern shape
- task-time assembly that composes:
  - procedures
  - concept notes
  - beliefs
  - supporting linked records
- `recall_first(...)` now surfaces applicable procedures and supporting concept and belief hits for issue-like local retrieval

## What Still Does Not Count As Done

These are intentionally still outside the shipped `0.9.0` scope:

- relation-aware ranking or graph traversal
- cross-domain concept synthesis beyond the current domain-local concept-note step
- automatic procedure promotion from raw session traces
- procedural execution or runtime orchestration
- active signal consumer loops
- pre-compaction capture before model-side loss

## Active Release Direction After 0.9.0

### 0.10 = relation-aware task memory

Status: shipped in `v0.10.0`.

#### Thesis

`0.10` should make task-time memory feel more connected and more explainable,
without turning relation-lite metadata into a graph-platform story.

#### What local work should cover first

- eligibility filtering for expired, future, and invalid relation targets before packet assembly
- anchor-first task assembly, with relation expansion happening after surviving anchors are chosen
- relation-weighted task assembly and ranking over the existing recall pool
- clearer use of `supports`, `contradicts`, `supersedes`, `depends_on`,
  `valid_from`, and `valid_until` during assembly
- packet-level suppression for superseded or contradicted records
- explainable inclusion/suppression reasons if the packet needs debugging

#### What must prove out

- reviewed relation-aware task-memory fixtures
- comparison cases where relation-aware packets beat flatter `0.9` packets
- contradiction, supersession, and validity-window leakage tests through `recall_first(...)`
- no MCP surface expansion required for the user-visible win

#### What it is not

- not a graph database
- not graph traversal
- not a new traversal-oriented MCP API
- not a claim that general retrieval ranking improved
- not cross-domain concept synthesis as the main `0.10` thesis

### 0.11 = governed procedure memory

Status: shipped in `v0.11.0`.

#### Thesis

Once task memory is more relation-aware, the next step is to make procedural
memory more trustworthy and less ad hoc.

#### Current local implementation

- procedure records now parse boundary and recovery fields:
  - `when_not_to_use`
  - `prerequisites`
  - `failure_mode`
  - `rollback_path`
- procedure governance status affects task packet assembly:
  - `validated` procedures receive a selection boost
  - `draft` procedures remain eligible but lower priority
  - `stale`, `replaced`, and `unsafe` procedures are suppressed
- a separate reviewed procedure-governance benchmark compares flat packets with
  governed procedure packets

This is a task-time governance layer, not a procedure execution engine or a
transcript-to-procedure autopromotion system.

#### What it should cover

- reviewed fixtures for procedure retrieval quality
- a narrow procedure curation or promotion workflow
- stronger procedure fields such as:
  - `when_to_use`
  - `when_not_to_use`
  - `prerequisites`
  - `failure_mode`
  - `rollback_path`
  - `supersedes`
- clearer procedure governance states such as draft, validated, stale, or replaced
- packet-level suppression for stale, replaced, and unsafe procedures
- backward-compatible warnings for older procedures without explicit status

#### What must prove out

- procedure retrieval quality is benchmarked, not anecdotal
- stale or unsafe procedures are easier to suppress
- procedure records stay compact and machine-first instead of drifting back toward transcript blobs

#### What it is not

- not automatic procedure execution
- not broad transcript-to-procedure autopromotion
- not a hidden agent runtime

### 0.12 = onboarding and integration hardening

Status: shipped in `v0.12.0`.

#### Thesis

After the memory engine becomes deeper and more governed, the next release should
make the first 5 minutes much easier for people who are not already inside the
Codex dogfood path.

#### What it shipped

- platform-neutral install and configuration docs
- a client support matrix with careful `verified`, `documented`, `locally tested`, and `supported` labels
- reusable config rendering for common MCP clients
- local `doctor` and `verify` commands for install confidence
- onboarding contract checks for docs, generated config fragments, and leak safety

#### What proved out

- a new user can install and verify the bridge without prior maintainer context
- generated client config fragments stay parseable and placeholder-safe
- the generic stdio path remains the stable lowest-common-denominator contract
- docs stop reading as Codex-only even while Codex stays the strongest reference workflow

#### What it is not

- not a new MCP tool surface
- not IDE plugin development
- not automatic client-config mutation
- not a claim that every client is verified just because the MCP shape is generic

### 0.13 = coordination under contention

Status: shipped in `v0.13.0`.

#### Thesis

Once onboarding is credible, the bridge should strengthen coordination semantics
under multi-consumer pressure without turning into a task-queue platform.

#### What it shipped

- `claim_signal(...)` is no longer a same-owner heartbeat path; active owners use `extend_signal_lease(...)`
- initial claims are capped by signal hard expiry, matching lease-extension behavior
- stale owners cannot `ack_signal(...)` after lease expiry; stale work must be reclaimed first
- generic claim selection filters eligible rows before the contention window so active claims do not starve later pending work
- failed explicit claims now return clearer reason codes such as `already-claimed`, `claimed-by-other`, `expired`, or `lease-expired`
- a signal contention benchmark covers unique active claims, stale ack blocking, stale reclaim, pending work under active-claim pressure, and hard-expiry lease caps

#### What proved out

- serialized multi-consumer contention fixtures pass with `signal_contention_case_pass_rate = 1.0`
- no duplicate active claims leak in the benchmark slice
- stale ownership boundaries are now explicit and test-covered
- public MCP surface stays at `10` tools

#### What it is not

- not a scheduler
- not an active consumer loop by default
- not a general queue platform
- not a distributed lock
- not exactly-once coordination

### 0.14 = governed learning candidates

Status: shipped in `v0.14.0`.

#### Thesis

`0.14` adds a policy-gated staging lane between runtime learning and durable AMB memory.

#### What it shipped

- v0 learning-candidate policy evaluation with `allow`, `needs_review`, `deny`, and `degraded_no_write` decisions
- internal `store_learning_candidate(...)` support for staging review records
- candidate status tags such as `candidate_status:pending` and `candidate_status:needs_review`
- storage-boundary policy recomputation so callers cannot forge allow decisions
- suppression of learning candidates from normal recall, browse, export, and stats unless review tags are explicitly requested
- authority-contract language that treats learning candidates as review material, not source-of-truth memory
- no new public MCP tools

#### What proved out

- safe low-authority candidates can be staged
- higher-authority classes route to review
- malformed, sensitive, raw-transcript, unsupported, or unsafe candidates are denied
- degraded no-write behavior can surface sanitized unsaved candidates when AMB is unavailable
- forged allow decisions are rejected at the store boundary
- candidate review queues can be queried through explicit tags without polluting ordinary memory recall

#### What it is not

- not automatic durable writeback from raw transcripts
- not a complete candidate review UI
- not a new public MCP learning API
- not a replacement for reviewed promotion or supersession flows

### 0.15 = reviewed memory revision gates

Status: shipped in `v0.15.0`.

#### Thesis

`0.15` adds deterministic proof that reviewed memory revision artifacts can
remain auditable without letting deleted, quarantined, obsolete, or review-lane
records become normal authority.

#### What it shipped

- deterministic learning review receipt hashes
- review receipts with `writeback_boundary:review_receipt_only`
- explicit `durable_mutation_performed_by_review: false`
- reviewed memory-evolution fixtures for supersession lineage, tombstone audit,
  quarantine, principal-scope warnings, bitemporal validity, and hidden review
  lanes
- release-contract coverage for the memory-evolution snapshot facts

#### What it is not

- not autonomous self-evolving memory
- not automatic durable promotion from raw transcripts
- not automatic deletion or retention compliance
- not a graph database or graph-memory platform

### 0.16 = reviewed memory operations queue

Status: shipped in `v0.16.0`.

#### Thesis

`0.16` makes hidden/stale/quarantined memory review work operator-visible
without widening the MCP surface or executing durable writes automatically.

#### What it shipped

- `agent-memory-bridge review-queue`, a CLI/report for staged candidates,
  learning reviews, tombstones, stale/expired records, and quarantined claims
- proposal-only writeback plans with `proposal_only_no_auto_mutation`
- deterministic review-queue benchmark snapshot with release-contract coverage
- explicit docs that `review-queue` is not a public MCP tool

#### What it is not

- not a review UI
- not automatic review, promotion, deletion, or writeback
- not a new MCP tool surface
- not a scheduler, worker runtime, or queue platform

### 0.17 = human review workflow

Status: shipped in `v0.17.0`.

#### Thesis

`0.17` turns reviewed memory operations into explicit human decision workflows
without making AMB an auto-reviewer, workflow runner, or larger MCP surface.

#### What it shipped

- `agent-memory-bridge review-workflow`, a CLI/report layered over
  `review-queue`
- decision prompts, manual steps, allowed outcomes, and blocked-until gates for
  each review item
- deterministic review-workflow benchmark snapshot with release-contract coverage
- explicit docs that `review-workflow` is not a public MCP tool and performs no
  automatic durable writeback

#### What it is not

- not a review UI
- not automatic approval, promotion, deletion, merge, or writeback
- not a new MCP tool surface
- not a scheduler, worker runtime, or queue platform

## 0.18 / 0.18.1 = AMB As The Unified Entry

Status: shipped across `v0.18.0` and `v0.18.1`.

### Thesis

AMB should become the user-facing product entry for durable memory plus
operator-friendly context views. AMH 0.8 remains useful incubator evidence,
especially the Task Brief language, but it should not keep rolling forward as a
separate product line unless there is a narrow migration reason.

The boundary stays:

- AMB owns durable memory, signals, governed mutation, review queues, and the
  stable MCP/export contract.
- AMH-style behavior may inform CLI/report UX, setup flows, and Task Brief
  rendering.
- AMH outputs are derived working context or proposal evidence, not durable
  authority.

### What Shipped

- `v0.18.0` added the read-only `task-brief` CLI/report over existing AMB
  assembly, review queue items, and active signals.
- `v0.18.1` added the `first-run` CLI/report so a new operator can see install
  steps, client config snippets, verification steps, and a first Task Brief
  preview without requiring AMH or widening the MCP surface.

Both releases keep the same boundary:

- no new public MCP tools
- no automatic client config mutation
- no automatic durable writeback
- no required AMH install for basic AMB use
- Task Briefs remain derived operator reports, not durable authority

### What To Reuse From AMH 0.8

- the Task Brief labels: `Used`, `Ignored`, `Needs Review`
- the read-only boundary language
- tests that block hype terms such as self-learning, agent brain, and automatic
  writeback
- the idea of copyable operator commands
- the distinction between startup packet previews and task-level Task Briefs

### What Not To Reuse Yet

- AMH as a separate public product release line
- AMH version/release history as part of AMB's product story
- runtime adapters, watcher wiring, scheduler behavior, or daemon behavior
- static packet fixtures as proof of live runtime integration
- direct AMB database access from helper code

## 0.19 = proof breadth and adoption proof

Status: shipped as `v0.19.0`.

### Thesis

`0.19` should not add another feature layer by default. It should prove that the
current product shape is broader than the original dogfood path: new users can
start through the unified AMB entry, and the existing retrieval/task-brief
behavior holds across a wider reviewed fixture set.

One sentence:

`0.19 = widen proof before widening product surface.`

### Scope

Start with a fixed denominator before implementation:

1. Before implementation, write and review
   [benchmark/v0.19-fixture-manifest.json](../benchmark/v0.19-fixture-manifest.json),
   which names every `0.19` fixture, its expected behavior, and its failure
   reason.
2. Add one reviewed `0.19` fixture pack with exactly `12` cases:
   - `4` retrieval cases covering durable decisions, gotchas, procedures, and
     concepts across more than one namespace.
   - `4` Task Brief cases covering `Used`, `Ignored`, `Needs Review`, and active
     signal inclusion without durable mutation.
   - `4` first-run/adoption cases covering generic stdio, Codex, Claude Code,
     and Cursor config guidance.
3. Add or update benchmark/report snapshots only for those cases.
4. Add release-contract facts for the new denominator and pass rates.
5. Update README proof facts only after snapshots exist.

Implementation checkpoint:

- [benchmark/latest-v0.19-adoption-proof-report.json](../benchmark/latest-v0.19-adoption-proof-report.json)
  now records the fixed `12`-case executable proof pack.
- The report is synthetic fixture evidence only. It does not claim clean-room
  external adoption or replace the post-release adoption gate.

### Acceptance Gate

`0.19` is ready only when all of these are true:

- `0.19` fixture count is exactly `12`.
- the 12 fixture names, expected behaviors, and failure reasons are reviewed
  in [benchmark/v0.19-fixture-manifest.json](../benchmark/v0.19-fixture-manifest.json)
  before fixture implementation starts.
- every fixture has a named expected behavior and failure reason.
- retrieval and Task Brief fixtures pass without adding MCP tools.
- first-run/adoption fixtures stay placeholder-safe and perform no client config
  writes.
- release contract passes and includes the new `0.19` fixture denominator.
- public MCP surface remains `10` tools.
- README and `README.zh-CN.md` facts match generated snapshots.
- CI is green on the final release commit.

### Non-Goals

These are explicitly out of scope for `0.19`:

- new public MCP tools
- new production CLI commands unless the maintainer explicitly re-scopes `0.19`
- client config writers or plugin installers
- review UI
- scheduler, watcher, worker, or runtime loop expansion
- automatic `store`, `promote`, `forget`, merge, approval, or writeback
- AMH as a required dependency or second public product line
- broad README redesign unrelated to the `0.19` evidence
- parallel research-track features unless they fit the predeclared 12-case
  denominator

### Stop Line

Stop the release when the acceptance gate passes and the tag/release exists.
Do not use a passing `0.19` gate as permission to start the next feature in the
same release. New ideas discovered during fixture work become later candidates
unless they are required to make a predeclared fixture pass.

### Failure Triggers

Block or redesign `0.19` if it:

- needs a new MCP tool to demonstrate value
- grows beyond the `12`-case denominator before the first release candidate
- starts fixture implementation before the 12 fixture names and expected
  outcomes are reviewed
- turns first-run into a config writer
- treats Task Briefs as durable memory authority
- makes AMH required for the normal install path
- improves README claims without updating generated proof snapshots

### Inherited Integration Failure Triggers

Block or redesign the integration if it:

- adds `startup_packet`, `task_packet`, or Task Brief MCP tools
- treats a generated Task Brief as durable memory authority
- performs automatic `store`, `promote`, `forget`, or review approval
- depends on AMH as a required second install for basic AMB use
- implies runtime integration, watcher/scheduler behavior, or live safety
  certification before a clean-room proof exists

## 0.20 = clean-room adoption proof

Status: recommended next release, not implemented.

### Thesis

`0.20` should close the main caveat left by `0.19`: the v0.19 adoption-proof
pack is synthetic fixture evidence, not clean-room external adoption. The next
release should prove that a fresh local environment can install AMB, start the
stdio MCP server, perform a minimal memory round trip, render first-run guidance,
and render a Task Brief without requiring AMH or writing client configuration.

One sentence:

`0.20 = clean-room adoption before product expansion.`

### Proposed Scope

1. Add a fixed clean-room proof runner that uses a temp home/store and launches
   AMB through the real local stdio entrypoint.
2. Exercise a small end-to-end path:
   - install/import sanity
   - `store -> recall` over MCP or the closest local stdio-compatible harness
   - `first-run` output
   - `task-brief` output
3. Emit machine-readable JSON and a short Markdown transcript.
4. Keep the public MCP surface at `10` tools.
5. Keep AMH optional and absent from the proof path.

### Non-Goals

- client certification claims
- plugin packages or client config writers
- watcher, scheduler, daemon, or runtime-loop behavior
- AMH as an install dependency
- native-memory vendor comparisons unless they fit a separately reviewed fixed
  denominator

### Acceptance Gate

`0.20` is ready only when:

- the clean-room proof is reproducible from a fresh temp store.
- the proof performs no client config writes.
- the proof performs only explicit demo memory writes in the temp store.
- public MCP surface remains `10` tools.
- release contract and public-surface checks pass.
- README wording says local reproducible proof, not external vendor adoption.

## Parallel Research Track

These are real gaps, but they do not need to become the thesis of the next full
release immediately:

- pre-compaction capture before model-side loss
- broader reviewed retrieval fixtures to keep credibility from overfitting the current corpus
- stronger write-side calibration for promotion quality
- deeper real multi-client contention dogfood beyond serialized benchmark cases
- human-facing review/promote ergonomics beyond the current CLI report

Treat these as cross-cutting tracks that can land only after a release gives
them a bounded denominator and stop line.

## What 1.0 Should Mean

`1.0` should not mean "we added more features." It should mean the current shape
is trustworthy enough to stabilize.

Before that label makes sense, the bridge should have:

- stable relation-aware task assembly
- measurable and governed procedure memory
- onboarding and install confidence for general MCP clients
- coordination semantics that survive contention
- a governed runtime-learning path with reviewable promotion boundaries
- a believable story for pre-compaction capture or its deliberate absence
- a still-small public MCP surface with a clear identity

## Guardrails

- keep the public MCP surface small unless workflow pressure clearly justifies expansion
- keep worker execution and scheduling outside the bridge core
- prefer machine-first durable artifacts over transcript growth
- keep local or private migration helpers out of the public repo story
- do not overclaim: relation metadata is not a graph engine, task assembly is not a full agent runtime, and learning candidates are not approved durable memory until reviewed/promoted
