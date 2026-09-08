# v0.33.1 Traceability

| Requirement | Implementation | Test/evaluation | Observed result |
|---|---|---|---|
| One canonical semantic invocation policy | `src/agent_mem_bridge/semantic_policy.py`; `docs/SEMANTIC-MEMORY-POLICY.md` | `tests/test_semantic_policy.py`; rendered Codex/OpenCode/generic outputs | Focused policy tests PASS; policy digest `4e2b343a03717300f65cd3c123bf641488c5279d7dad8290da7705b237c4ad3c` |
| Thin portable host adapters | `memory-policy --host codex|opencode|generic`; manual/export-first placement | `tests/test_cli_semantic_policy.py`; `host-placement.md` | CLI renderer tests PASS; placement observed, semantic host trials NOT RUN / UNOBSERVED |
| Observable invocation failures | `src/agent_mem_bridge/invocation_evidence.py`; existing bounded run-event compatibility | `tests/test_invocation_evidence.py`, `tests/test_semantic_eval.py` | Sanitization and layer attribution tests PASS; no transcript or hidden-reasoning capture |
| Frozen cross-host evaluation | `benchmark/semantic-invocation-v1.json`; frozen rubric/hash; `semantic_eval.py` | `scripts/run_semantic_invocation_eval.py`; generated report | Rubric hash PASS. Synthetic fixture evidence prefers C over B on governance/stale-memory handling. Live Codex/OpenCode/ChatGPT trials remain NOT RUN / UNOBSERVED. |
| Reduced active documentation authority | Canonical `INSTALL_FOR_AGENTS.md`; pointer `llms-install.md`; map `llms.txt`; audited cleanup | onboarding/public-surface tests; `artifact-consumer-graph.md` | Focused onboarding/public tests PASS; nine audited files removed, regression fixtures retained |
| Preserve v0.33 product boundary | No schema, MCP, durable-authority, automatic-learning, or host-runtime change | release/public/onboarding contracts and boundary tests | Schema v12 and exactly 17 public tools remain; no MCP tool #18; no automatic writeback |
