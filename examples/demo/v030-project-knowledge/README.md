# v0.30 Project Knowledge Activation demo

This reproducible local demo shows one narrowly bounded activation path: code
tells **WHAT**; conversations teach **WHY**.

1. It makes a clean, committed temporary Git fixture and runs the real `bootstrap-repo` command to bind a derived, rebuildable, commit-bound repository **WHAT** fact (`python_requires >=3.11`).
2. A real MCP stdio process **B** calls the public `store` tool to persist one explicit project-decision **WHY**: keep the committed single-node fixture local-first and do not introduce Redis.
3. After B exits, a fresh real MCP stdio process **C** calls public `recall` and must receive both that project decision and the commit-bound repository WHAT sidecar.
4. The real read-only `inspect` command reports supported repository WHAT provenance, its `derived_repository` authority, its current binding, and its mutation boundary. It does not make a durable-decision selection claim.

The runner isolates `AGENT_MEMORY_BRIDGE_HOME`, `AGENT_MEMORY_BRIDGE_DB_PATH`, `AGENT_MEMORY_BRIDGE_LOG_DIR`, `AGENT_MEMORY_BRIDGE_REPOSITORY_SNAPSHOT_ROOT`, and `AGENT_MEMORY_BRIDGE_CONFIG` in a temporary directory. It explicitly sets lexical retrieval in both configuration and environment. It uses no direct SQLite writes.

Run it from the repository root with Git available on `PATH` and the Python environment in which this package and its documented dependency are installed:

```bash
<venv-python> examples/demo/v030-project-knowledge/run_demo.py
<venv-python> examples/demo/v030-project-knowledge/run_demo.py --json
```

The default output is a compact four-stage `PASS`/`FAIL` story: code tells the
derived, rebuildable, commit-bound WHAT; conversations provide the explicit
project-decision WHY; fresh MCP recall returns both; and `inspect` explains the
supported repository/provenance boundary. `--json` emits the same bounded,
deterministic evidence and omits temporary paths, generated IDs, timestamps,
and commit hashes.

This is a local temporary-fixture proof only. It does not prove:

- automatic learning;
- automatic durable repository writeback;
- that a model applied recalled memory;
- that recalled memory caused an outcome;
- agent productivity;
- external client adoption or client identity certification;
- recall quality, repository completeness, production readiness, or multi-user coordination behavior.
