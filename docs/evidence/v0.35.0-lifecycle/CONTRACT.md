# v0.35.0 — Governed Lifecycle Activation

## Frozen release scope

Package the completed lifecycle initiative without new product features or a
new eval campaign. Product/canonical baseline:
`db9923508cde457c4b3181d8861a4785fd5cc065`, the #62 merge after #50/#51 closure.
The release PR changes only version identity, documentation and existing
release-version assertions. Runtime, adapter, fixture, expected-decision,
measurement, grader and cohort-binding bytes remain unchanged.

## Invariants and evidence

| Invariant | Frozen outcome and evidence boundary |
| --- | --- |
| Durable schema | v12; no migration in this release. |
| Public MCP tools | Exactly 17; schema digest `24c5c52321d61b4b6f647c0d74e2d8304ca68716c403e08a274e9badfd8dc9f8`. |
| Automatic durable promotion | No; Stop capture produces a hidden review candidate, not ordinary durable memory. No automatic learning. |
| Governed resolver | Required for scoped activation; confirmed binding owns namespace selection, hooks do not bind/rebind. #52 source and resolver tests. |
| Codex matched lifecycle | **10 PASS / 0 FAIL / 0 INCONCLUSIVE**; [report](../issue-51-codex-matched/report.json), [receipt](../issue-51-codex-matched/receipt.json). |
| Remote authority semantics | PASS for hit, skip, no-hit, error and local-fallback trap in the [historical #58 native proof](../issue-58-codex-remote/README.md); current matched #51 receipts also prove remote hit/no-hit/error. Configured remote never falls back to local SQLite. |
| Write-side review candidate | PASS in [historical #53 native Stop proof](../issue-53-codex-stop/README.md): positive creates one hidden `needs_review` candidate, negative creates zero, ordinary memory unchanged. Local authority only; remote capture blocked. |
| Plain MCP independence | Preserved; hooks are optional, stdio/public surface remains unchanged. Release-PR wheel/sdist acceptance must prove installed behavior. |
| OpenCode prompt activation | **NOT_RUN / pending**; bounded runnable path retained. Not certified by Codex evidence. |

Read-side scope does not require a clean checkout merely to identify its
confirmed binding; stale/dirty derived repository WHAT remains ineligible
under its separate contract. Live inspected repository evidence takes
precedence over conflicting/stale recalled guidance. Unbound, ambiguous and
unreadable bindings fail closed.

## Matched measurement identity

Codex CLI 0.160.0 / `gpt-6-luna` / `adapter_enabled` / `remote_loopback` /
`invocation_inline` / frozen v2, same ten cases and expected decisions,
`codex-repo-read-v1`, unchanged grader, 150-second native timeout,
210-second outer timeout, two-case parallelism and the same required
isolation contract. The original CLI and `codex-cohort-v1` remain frozen;
the separate repaired collector is
`scripts/run_codex_lifecycle_activation_benchmark.py` on tag `v0.35.0`.
Current Core does not ship that collector.

Chronology is immutable:

| Evidence | Result |
| --- | --- |
| Historical #50 baseline and revised re-score | 3 PASS / 7 FAIL; original verdict retained, no new model calls. |
| Isolation-rejected pilot | 1 completed / 9 NOT_RUN; not a matched PASS. |
| Repaired source `58b1d60` | 8 PASS / 2 FAIL; retained unchanged. |
| Repaired source `483c755e7299b96990b0a75f4363027c21138c47` | 10 PASS / 0 FAIL; same measurement and grader. |

Final report SHA-256:
`673db1d1a5bb3fbf22da6dbf1c6b6bb17ba6a7949f7879e1bbde5a480d6e267d`.
Final receipt SHA-256:
`47928e0d5c16ee6210225281dadaf845dab9cfe6f2a1c9b7f88f70c621ce40f8`.

The #51 merge tree is byte-identical to reviewed head `3a10424`.
[Canonical post-merge verification](https://github.com/zzhang82/Agent-Memory-Bridge/issues/51#issuecomment-5972120034)
validated 99 source hashes and the report/receipt identities, then reproduced
the report with the canonical grader and retained observations. It was not a
new host trial. Main CI at that merge passed. Release identity changes must
not silently relabel older native trials as freshly executed 0.35.0 trials.

The #53 Stop proof retains its source manifest at historical merge
`bfb85048b40404259710a14e68556dd85398a8e6` (Codex 0.157.1 / Luna / inline).
The #58 remote proof retains its manifest at historical merge
`73529b052f86a285a36f00376b5829b8ef74444e` (Codex 0.160.0 / Luna / inline).
#51 subsequently changed shared read-side files, so those older manifests
must not be rewritten or presented as exact-current-source manifests.
The release PR preserves the current runtime bytes; current tests cover Stop
capture and remote authority regressions. Neither source tests nor retained
historical evidence are a fresh native certification of those lanes.

## Distribution gates

1. Release PR: full CI, release-cut acceptance, clean installed wheel/sdist
   stdio checks and version/docs consistency. These are release-version gates,
   separate from the completed product acceptance.
2. Authorized merge: `v0.35.0` resolves to the **release merge SHA**, not the
   earlier product merge or a local pre-merge commit.
3. Published GitHub Release: existing PyPI Trusted Publishing and GHCR
   workflows must verify matching package/tag/immutable source identity.
4. Verify live PyPI package and GHCR image digest/revision label. The current
   workflow publishes a versioned image with the release SHA label; it does
   not promise a separate SHA-named image tag.
5. Fresh venv: install `agent-memory-bridge==0.35.0` from PyPI, check installed
   metadata/version and `agent-memory-bridge --help`. Publication and smoke
   remain unobserved until actually executed.

## Retained limits and authority

Activation is bounded rule-based behavior, not broad language detection or a
productivity claim. OpenCode native activation, native Windows activation,
project/plugin discovery and mixed/concurrent callback association are not
certified. Windows CI proves compatibility, not host callback acceptance.
The frozen repeated case verifies recall budget; exact-material-repeat and
compaction remain separate auxiliary evidence. Remote capture, transcript
archiving, hidden-reasoning persistence, new durable authority, schema v13,
tool #18 and automatic durable promotion are excluded.

No production upgrade, database replacement, credential/configuration
change or worktree cleanup is authorized by this document.
Tagging and publication are owner-gated steps after release-PR review/merge.
