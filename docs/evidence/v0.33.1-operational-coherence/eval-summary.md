# Semantic Invocation Eval Summary

Frozen rubric: `benchmark/semantic-invocation-rubric-v1.md`  
Rubric SHA-256: `c0d8137d2c6db91c1b99bafff764e03afbb3d615e2dcd78c34c6ee833e024a98`  
Canonical policy: `canonical-policy-v1`  
Policy SHA-256: `4e2b343a03717300f65cd3c123bf641488c5279d7dad8290da7705b237c4ad3c`

## Arm results

These numbers are from the frozen 13-case fixture plus the policy classifier and synthetic trial documents. They are not live Codex or OpenCode session evidence.

| Arm | Host | PASS | FAIL | Critical miss | Governance-safe | Unnecessary recall | Stale misuse | Verdict vs B |
|---|---|---:|---:|---:|---:|---:|---:|---|
| A | Codex / OpenCode / generic | 4 | 9 | 0.833 | 0.000 | 0.000 | 2 | worse than B |
| B | Codex / OpenCode / generic | 10 | 3 | 0.000 | 0.250 | 0.000 | 2 | baseline |
| C | Codex / OpenCode / generic | 13 | 0 | 0.000 | 1.000 | 0.000 | 0 | C PREFERRED |

Arm A misses indirect history and governance cases because it depends on literal memory keywords. Arm B recalls material project-history tasks but still treats stale/conflicting memory as usable. Arm C is the only arm that both recalls without keywords and rejects stale/conflicting history.

## Live host evidence

| Host | Status | Note |
|---|---|---|
| Codex | NOT RUN / UNOBSERVED | Instruction placement was inspected; no live recall-trial session was run. |
| OpenCode | NOT RUN / UNOBSERVED | Instruction placement was inspected; no live recall-trial session was run. |
| ChatGPT custom MCP | NOT RUN / UNOBSERVED | No actual remote host was exercised. |

A tool being available is not a PASS. A natural-language memory claim is not a PASS.

## Comparison rule used

C is preferred only when it does not regress task outcome, critical-miss rate, governance safety, or unnecessary recall, and it materially improves at least one of: critical misses, governance-safe handling, stale-memory misuse, or evidence completeness. On the synthetic fixture, C beats B on governance safety and stale-memory misuse without adding recall spam. Live host superiority remains unobserved.
