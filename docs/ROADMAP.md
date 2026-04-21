# Roadmap

Last updated: 2026-04-19 (America/New_York)

This maintainer note tracks the shipped ladder through `0.9.0` and the next
likely pressure points after it.

## Shipped Ladder

- `0.7 = governed learning layer`
- `0.8 = credible, structured memory`
- `0.9 = applicable, compositional memory`

The planned `0.8` work shipped as part of the direct `0.9.0` release rather than
as a separate public tag.

## What 0.9.0 Now Covers

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

## Planned Release Ladder After 0.9.0

### 0.10 = relation-aware task memory

#### Thesis

`0.10` should make task-time memory feel more connected and more explainable,
without turning relation-lite metadata into a graph-platform story.

#### What it should cover

- relation-aware recall expansion beyond simple supporting-ID fetch
- relation-weighted task assembly and ranking
- clearer use of `supports`, `contradicts`, `supersedes`, `depends_on`,
  `valid_from`, and `valid_until` during assembly
- a first careful pass at cross-domain concept synthesis

#### What must prove out

- reviewed relation-aware task-memory fixtures
- comparison cases where relation-weighted assembly beats flatter packets
- contradiction-aware recall cases
- no MCP surface expansion required for the user-visible win

#### What it is not

- not a graph database
- not graph traversal
- not a new traversal-oriented MCP API

### 0.11 = governed procedure memory

#### Thesis

Once task memory is more relation-aware, the next step is to make procedural
memory more trustworthy and less ad hoc.

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

#### What must prove out

- procedure retrieval quality is benchmarked, not anecdotal
- stale or unsafe procedures are easier to suppress
- procedure records stay compact and machine-first instead of drifting back toward transcript blobs

#### What it is not

- not automatic procedure execution
- not broad transcript-to-procedure autopromotion
- not a hidden agent runtime

### 0.12 = coordination under contention

#### Thesis

After memory becomes more connected and more actionable, the next release should
strengthen coordination semantics under real multi-agent pressure without turning
the bridge into a task-queue platform.

#### What it should cover

- retry boundaries
- contention benchmarks
- clearer reclaim / retry / abandon semantics
- stronger provenance around multi-agent handoff and ownership transitions
- only if pressure justifies it: narrow dead-letter-lite or retry-reason support

#### What must prove out

- contention harnesses with multiple consumers
- retry and reclaim regression coverage
- fairness behavior stays legible under repeated polling
- subagent or multi-client handoff examples are real enough to matter

#### What it is not

- not a scheduler
- not an active consumer loop by default
- not a general queue platform

## Parallel Research Track

These are real gaps, but they do not need to become the thesis of the next full
release immediately:

- pre-compaction capture before model-side loss
- broader reviewed retrieval fixtures to keep credibility from overfitting the current corpus
- stronger write-side calibration for promotion quality

Treat these as cross-cutting tracks that can land in `0.10.x` through `0.12.x`
as they become stable and portable.

## What 1.0 Should Mean

`1.0` should not mean “we added more features.” It should mean the current shape
is trustworthy enough to stabilize.

Before that label makes sense, the bridge should have:

- stable relation-aware task assembly
- measurable and governed procedure memory
- coordination semantics that survive contention
- a believable story for pre-compaction capture or its deliberate absence
- a still-small public MCP surface with a clear identity

## Guardrails

- keep the public MCP surface small unless workflow pressure clearly justifies expansion
- keep worker execution and scheduling outside the bridge core
- prefer machine-first durable artifacts over transcript growth
- keep local or private migration helpers out of the public repo story
- do not overclaim: relation metadata is not a graph engine, and task assembly is not a full agent runtime
