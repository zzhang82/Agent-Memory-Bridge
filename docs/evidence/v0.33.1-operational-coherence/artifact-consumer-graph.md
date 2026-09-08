# Artifact Consumer Graph

Frozen from the v0.33.0 tree before cleanup. The census used `git ls-files`,
`git grep`, the public/onboarding inventories, the release contract, and the
generator paths. A reference in a test or inventory is recorded as a consumer,
but is not assumed to be a real product invariant until its purpose is checked.

| Artifact | Current purpose | References / consumers | Executable consumer? | Contract or public consumer? | Reproducible? | Classification and reason |
|---|---|---|---|---|---|---|
| `INSTALL_FOR_AGENTS.md` | Full install-to-first-success procedure | README pair, `llms.txt`, onboarding contract, public inventory, release-candidate tests | No; read by humans/agents | Yes | Source text | **Keep as canonical**; make it the single detailed install authority. |
| `llms-install.md` | Short install procedure plus repeated release/history facts | `INSTALL_FOR_AGENTS.md`, `docs/INTEGRATIONS.md`, `docs/CONFIGURATION.md`, `llms.txt`, onboarding/public/release tests | No | Yes | Source text | **Keep as compatibility pointer**; external links make deletion needlessly breaking, but remove duplicate procedure and point to the canonical guide. |
| `llms.txt` | Agent navigation/index | onboarding/public inventories and the old harness note | No | Yes | Source text | **Keep and shrink** to navigation, current boundaries, and canonical links; it must not be a second install guide. |
| `docs/HARNESS-DESIGN.md` | Historical AMB/AMH boundary and packet design note | `llms.txt`, onboarding/public inventories, onboarding fixtures | No | Yes, but only through inventories | Source text | **Delete from active tree and inventories** after retaining live boundary language in `docs/ARCHITECTURE.md`/`docs/CONTEXT-ASSEMBLY.md`; it says “13 MCP tools” and describes a future split that is no longer current. Git history preserves it. |
| `docs/STARTUP-PROTOCOL.md` | Older system-profile startup doctrine | Only blocked-link inventory in `public_surface.py` | No | No active consumer | Source text | **Delete**; its useful recall boundary becomes the canonical portable policy, while the file is not a current public entry point. |
| `docs/CONVERSATION-INGEST.md` | Early Codex conversation-ingest direction | Only blocked-link inventory in `public_surface.py` | No | No active consumer | Source text | **Delete**; it is superseded by the explicit no-automatic-learning policy and has no implementation contract. |
| `docs/MODEL-ROUTING.md` | Local model-routing advice | Only blocked-link inventory in `public_surface.py` | No | No active consumer | Source text | **Delete**; model selection is host/operator configuration, not AMB product authority, and the document contains stale model guidance. |
| `docs/PYPI-PUBLISHING.md` | One-time v0.32.1 publication instructions | No repository consumer | No | No | Source text, now historical | **Delete**; current release workflow, `docs/PRODUCTION-STATUS.md`, and versioned announcements are the active publication references. |
| `docs/v0.31-release-proof.md` | Old v0.31 distribution proof instructions | No repository consumer; proof script remains available | The referenced script is executable | No current contract consumer | Reproducible by `tools/evidence/v031_release_smoke.py` | **Delete the orphan narrative**; retain executable evidence code and Git history. |
| `examples/harness-preview/` | Static startup/task packet examples | Only `public_surface.py` inventory | No | Inventory only | Static and sanitized | **Delete from active examples**; current docs explicitly say packet names are internal and no packet MCP tools exist, so these examples create a misleading product surface without a live consumer. |
| `benchmark/latest-report.json` | Current retrieval/regression snapshot | `scripts/run_benchmark.py`, release contract, benchmark docs | Yes | Release contract | Yes, with timing variance | **Keep**; current regression guard. |
| `benchmark/latest-proof-report.json` | Current deterministic proof snapshot | `scripts/run_deterministic_proof.py` | Yes | No direct release-contract consumer | Yes, with timing variance | **Keep for now**; it is current evidence, not obsolete historical narrative. Do not promote timing fields to product claims. |
| `benchmark/latest-adversarial-memory-report.json` | Governance realism snapshot | adversarial runner, benchmark docs, release contract, public inventory | Yes | Yes | Yes | **Keep**; release-facing governance regression evidence. |
| `benchmark/latest-v0.19-adoption-proof-report.json` + `v0.19-fixture-manifest.json` | Fixed-denominator adoption proof | `tools/evidence/v019_adoption_proof.py`, tests, release contract, sdist boundary, benchmark docs/public inventory | Yes | Yes | Yes | **Keep**; executable fixture and denominator are still contract evidence. |
| `benchmark/latest-v0.20-clean-room-proof-*` + `docs/v0.20-clean-room-proof.md` | Clean-room distribution/stdIO proof | `tools/evidence/v020_clean_room_proof.py`, tests, release contract, public inventory, changelog | Yes | Yes | Yes | **Keep**; report/transcript/doc form one reproducible proof set. |
| `benchmark/latest-v0.21-governed-change-report.json` + manifest | Fixed governed-change regression proof | `tools/evidence/v021_governed_change_proof.py`, tests, release contract, public inventory, changelog | Yes | Yes | Yes | **Keep**; the manifest protects the denominator and the report is checked evidence. |
| `docs/v0.27.2-announcement.md`, `v0.27.3`, `v0.27.4` | Detailed historical authority milestones | changelog, release contract, public inventory, tests | No | Yes | Historical source text | **Keep as retained historical evidence**; these are explicitly named by current contracts and not duplicate install guidance. |
| `docs/v0.31.1-announcement.md`, `v0.32.0`, `v0.32.1`, `v0.32.2`, `v0.33.0` | Version-specific release narratives | changelog/tests; selected public inventory | No | Yes for retained records | Historical source text | **Keep**; GitHub Releases is the external publication archive, but these files still have current contract or traceability value. |
| Other `benchmark/latest-*.json` snapshots | Current benchmark/proof reports | benchmark docs, release contract, or dedicated tests | Yes | Usually yes | Yes | **Keep unless a later graph proves no executable or review consumer**; do not delete by age alone. |

## Decisions from the graph

1. The current install authority is `INSTALL_FOR_AGENTS.md`.
2. `llms-install.md` remains only as a small compatibility entry point, not a
   synchronized copy.
3. `llms.txt` becomes a map, not a release-history or install duplicate.
4. Historical release/proof artifacts with active executable or contract
   consumers stay in place.
5. After this audit, the seven marked cleanup targets were removed from the
   active tree as nine files (the three files under `examples/harness-preview/`).
   Their source history remains recoverable in Git; executable regression
   fixtures and contract-backed historical evidence were retained.
