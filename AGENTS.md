# Agent Memory Bridge Contributor Instructions

These instructions are self-contained. Contributors only need this repository,
Python, and the documented toolchain; no external profile, memory service, or
machine-specific setup is required.

## Setup And Checks

- Use Python 3.11 or newer.
- Install dev dependencies from the repo root:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

- Run the main test suite:

```bash
python -m pytest
```

- Run lint and format checks:

```bash
ruff check src tests scripts
ruff format --check src tests scripts
```

- Run the Linux CI typecheck target. On Windows, add `--platform linux` so
  mypy checks the same platform contract as CI instead of rejecting POSIX-only
  process APIs during local analysis:

```bash
mypy src/agent_mem_bridge/schema.py src/agent_mem_bridge/repository.py src/agent_mem_bridge/signals.py src/agent_mem_bridge/query.py src/agent_mem_bridge/state_io.py src/agent_mem_bridge/command_provider.py src/agent_mem_bridge/database_maintenance.py
```

- For release-facing or public-surface changes, also run:

```bash
python ./scripts/run_benchmark.py
python ./scripts/run_deterministic_proof.py
python ./scripts/check_release_contract.py
python ./scripts/check_public_surface.py
python ./scripts/check_onboarding_contract.py
```

## Architecture Boundaries

- Runtime code lives in `src/agent_mem_bridge`; tests live in `tests`; proof and
  regression fixtures live in `benchmark`; operational entry points live in
  `scripts`.
- `server.py` defines the public MCP surface. Keep the tool contract small and
  explicit: `store`, `recall`, `browse`, `stats`, `forget`, `promote`,
  `annotate`, `revise`, `export`, `claim_signal`, `extend_signal_lease`, and
  `ack_signal`.
- `storage.py`, `repository.py`, and `schema.py` own durable record behavior.
  `signals.py` owns Signal lifecycle rules. `query.py` and index modules own
  retrieval and derived indexes.
- CLI reports, review queues, Task Briefs, activation receipts, benchmarks, and
  context assembly are not separate public MCP tools unless a reviewed contract
  change explicitly adds them.
- Worker execution, hosted identity, OAuth, per-namespace ACLs, sandboxing,
  remote multi-user coordination, and distributed locking are outside the core
  bridge boundary unless the implementation and public docs both change.

## Mutation And Migration Invariants

- SQLite/WAL database rows are the durable authority. Exports, dashboards,
  reports, compiled context, FTS rows, and embedding sidecars are derived views
  or caches.
- Rebuilding FTS or embedding indexes must not change durable memory or Signal
  row counts or content.
- Durable memory and coordination Signals are separate lanes. Signal lifecycle
  fields belong only to `kind="signal"` operations; compatibility cleanup at the
  MCP boundary must not weaken the lower-level storage contract.
- `annotate` may add auditable metadata, non-policy tags, and provenance without
  rewriting original content. `revise` must create a successor and supersession
  audit link in one transaction.
- Callers must not mint reserved governance tags, convert hidden review lanes
  into durable authority, or bypass policy checks through supplied metadata.
- `forget`, revision, restore, backup, and migration work must preserve audit
  semantics, redaction boundaries, lineage/projection repair, and rollback paths.
- Schema or migration changes need focused tests, fixture updates when snapshots
  intentionally change, and an explicit compatibility path for stored data.

## Benchmark Expectations

- Treat checked-in benchmark reports as regression guards, not broad leaderboard
  or productivity claims.
- Do not update snapshot files only to make a failing check pass. Explain the
  behavior change and update tests, fixtures, README facts, and release-contract
  expectations together.
- Public claims about recall quality, Signal contention, governed change,
  review workflow behavior, onboarding, or MCP tool count must be backed by the
  relevant script output and release checks.

## Contribution Rules

- Keep changes scoped and reversible. Do not mix runtime experiments with public
  contract, schema, or release-doc changes.
- Add or update tests when behavior changes.
- Do not commit local runtime state, databases, private paths, generated caches,
  or machine-specific reports.
- Before changing security, provenance, or authority language, read
  `SECURITY.md`, `docs/TRUST-BOUNDARY.md`, and `docs/AUTHORITY-CONTRACT.md`.
