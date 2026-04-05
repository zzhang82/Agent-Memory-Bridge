# Contributing

Thanks for helping improve Agent Memory Bridge.

## Development Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## Useful Commands

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run the stdio smoke test:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_stdio.py
```

Run the health check:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_healthcheck.py --report-path .\examples\healthcheck-report.json
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

## Privacy And Artifacts

- Do not commit local runtime state, databases, or machine-specific reports.
- Keep private migration notes outside the public repo.
- Use generic example paths in docs.
