# v0.33.0 Baseline Record

Frozen before v0.33.1 implementation work on 2026-09-07.

## Source identity

| Field | Observed value |
|---|---|
| Baseline label | `v0.33.0` source/release line |
| Baseline commit | `cb314ebbf96d48c13c0c298f6ac7273d701682f4` |
| Baseline branch used for work | `codex/v0331-operational-coherence` |
| Checked-out tag decoration | `v0.33` (the commit is the v0.33.0 release merge; no local `v0.33.0` tag was present) |
| Package version | `0.33.0` from `pyproject.toml` |
| Schema | v12 from `src/agent_mem_bridge/schema.py` |
| Public MCP tools | 17; order and digest are checked by the release contract |
| Public tool-schema digest | `24c5c52321d61b4b6f647c0d74e2d8304ca68716c403e08a274e9badfd8dc9f8` |
| Test collection | 1059 tests |

The separate parent checkout was left untouched. It is on
`docs/readme-governed-project-memory` at `a82d20ce` with user-owned untracked
`.opencode/` and `pip/` paths. This worktree starts from the actual v0.33.0
source line instead.

## Baseline validation

| Check | Result | Evidence |
|---|---|---|
| `pytest --collect-only -q` | PASS | 1059 tests collected in 18.11s |
| `pytest` | PASS | 1059 passed in 461.19s |
| `ruff check src tests scripts` | PASS | `All checks passed!` |
| `ruff format --check src tests scripts` | PASS | 244 files already formatted |
| CI Linux mypy target | PASS | `Success: no issues found in 17 source files` |
| `scripts/check_release_contract.py` | PASS | package 0.33.0, 17 tools, test count 1059, no mismatches |
| `scripts/check_public_surface.py` | PASS | no violations |
| `scripts/check_onboarding_contract.py` | PASS | all seven checks passed |
| `scripts/run_benchmark.py` | PASS | 11 questions; memory top-1 1.0; MRR 1.0; signal and relation checks passed |
| `scripts/run_deterministic_proof.py` | PASS | 4/4 checks; signal and relation checks passed; duplicate suppression 1.0 |

The benchmark and deterministic-proof runners rewrite timing fields in their
checked-in snapshots. Their outputs passed, but timing-only changes were
reverted to the baseline bytes immediately; no baseline report diff remains.

## Current invocation and host evidence

- `src/agent_mem_bridge/client_config.py` renders MCP connection fragments for
  generic stdio, Codex TOML, and OpenCode JSON among the supported clients.
- `src/agent_mem_bridge/setup_planner.py` and `setup_apply.py` preview/apply
  client connection configuration. They explicitly do not establish that a
  host loaded the configuration or called `recall`.
- Existing run evidence already has bounded `memory_recalled`,
  `memory_applied`, and `memory_rejected` event types plus receipt-bound
  attribution. No canonical cross-host semantic invocation policy was present
  in the baseline tree.
- Baseline host status is `Codex: verified reference client`,
  `OpenCode: locally tested`, and `generic stdio: supported` in
  `docs/INTEGRATIONS.md`. These are connection/configuration statuses, not
  proof that the host made a correct memory decision.

## Boundary freeze

The v0.33.1 work must preserve schema v12, the exact 17-tool public MCP
surface, SQLite/WAL durable authority, local-first operation, and the
no-automatic-learning/no-automatic-writeback boundary. Policy and evaluation
artifacts are derived or proposal-only unless an existing governed AMB path is
explicitly used.
