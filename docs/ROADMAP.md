# Roadmap

Last updated: 2026-04-28 (America/New_York)

This maintainer note tracks the shipped ladder through `0.13.0`, plus likely
post-0.13 research tracks. Treat it as a maintainer planning document, not as the
public release contract.

## Shipped Ladder

- `0.7 = governed learning layer`
- `0.8 = credible, structured memory`
- `0.9 = applicable, compositional memory`

The planned `0.8` work shipped as part of the direct `0.9.0` release rather than
as a separate public tag.

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

### 0.14 = likely next direction

Status: candidate direction, not committed scope.

The strongest remaining candidates are:

- pre-compaction capture before model-side context loss
- broader reviewed retrieval fixtures so credibility does not overfit the current corpus
- deeper real multi-client contention dogfood beyond serialized benchmark cases
- stronger write-side calibration for promotion quality

Pick one thesis when the next release starts. Do not bundle all of these into one
release unless they share a clear proof story.

## Parallel Research Track

These are real gaps, but they do not need to become the thesis of the next full
release immediately:

- pre-compaction capture before model-side loss
- broader reviewed retrieval fixtures to keep credibility from overfitting the current corpus
- stronger write-side calibration for promotion quality

Treat these as cross-cutting tracks that can land in `0.14.x` and later
as they become stable and portable.

## What 1.0 Should Mean

`1.0` should not mean "we added more features." It should mean the current shape
is trustworthy enough to stabilize.

Before that label makes sense, the bridge should have:

- stable relation-aware task assembly
- measurable and governed procedure memory
- onboarding and install confidence for general MCP clients
- coordination semantics that survive contention
- a believable story for pre-compaction capture or its deliberate absence
- a still-small public MCP surface with a clear identity

## Guardrails

- keep the public MCP surface small unless workflow pressure clearly justifies expansion
- keep worker execution and scheduling outside the bridge core
- prefer machine-first durable artifacts over transcript growth
- keep local or private migration helpers out of the public repo story
- do not overclaim: relation metadata is not a graph engine, and task assembly is not a full agent runtime
