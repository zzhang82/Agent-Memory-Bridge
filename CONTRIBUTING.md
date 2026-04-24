# Contributing

Thanks for helping improve Agent Memory Bridge.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .[dev]
```

If your shell uses a different activation command, use that instead.

## Useful Commands

Run tests:

```bash
python -m pytest
```

Run deterministic proof:

```bash
python ./scripts/run_deterministic_proof.py
```

Run the benchmark:

```bash
python ./scripts/run_benchmark.py
```

Run the stdio smoke test:

```bash
python ./scripts/verify_stdio.py
```

Run the health check:

```bash
python ./scripts/run_healthcheck.py --report-path ./.runtime/healthcheck-report.json
```

## Project Shape

- Keep the core bridge small.
- Treat `store` and `recall` as the stable MCP foundation.
- Build worker execution and task routing as a separate layer on top.
- Prefer machine-readable records over prose-heavy summaries.

## Pull Request Guidance

- Keep changes scoped and reversible.
- Add or update tests when behavior changes.
- Avoid mixing runtime-path experiments with core contract changes.
- Preserve compatibility names unless a migration path is explicit.

## Public Surface Boundary

Treat the public repo surface and the local dogfood lab as different lanes.

Public core:

- release-facing docs and examples
- benchmark and proof artifacts that support public claims
- the small MCP tool surface and the released runtime behind it

Private lab or maintainer-only:

- operator-specific startup doctrine
- local cutover and migration helpers
- archived release notes, planning notes, and private rollout scratch work

Before a public tag or push, run both checks:

```bash
python ./scripts/check_release_contract.py
python ./scripts/check_public_surface.py
```

If a file needs personal operator names, local migration assumptions, or machine
paths to make sense, keep it out of the public docs index and out of the public
release story.

## Privacy And Artifacts

- Do not commit local runtime state, databases, or machine-specific reports.
- Keep private migration notes outside the public repo.
- Use generic example paths in docs.
