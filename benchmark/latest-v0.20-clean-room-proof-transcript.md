# v0.20 Clean-Room Adoption Proof

- schema: `memory.v0_20_clean_room_proof.v1`
- release: `0.20.0`
- proof_kind: `local_clean_room_adoption_not_vendor_certification`
- ok: `true`
- case_count: `6`
- pass_count: `6`
- pass_rate: `1.0`
- public_mcp_tool_count: `10`
- public_mcp_surface_change: `false`
- client_config_write_count: `0`
- explicit_demo_memory_write_count: `1`
- explicit_demo_signal_write_count: `0`
- non_demo_durable_writeback_count: `0`
- amh_required: `false`
- external_vendor_adoption_claim: `false`

## Cases

### v020-local-entrypoint-import

- category: `install_import`
- passed: `true`
- command_or_query: `python -m agent_mem_bridge --version`
- expected_behavior: `python -m agent_mem_bridge --version` exits 0 with a package version.
- failure_reason: `none`
- non_goal_guard: Does not claim PyPI, vendor-client, or external clean-room certification.

### v020-stdio-tool-surface

- category: `stdio_mcp`
- passed: `true`
- command_or_query: `MCP list_tools over python -m agent_mem_bridge`
- expected_behavior: MCP `list_tools` returns the documented 10 tools and no v0.20 tool.
- failure_reason: `none`
- non_goal_guard: Does not add startup_packet, task_packet, plugin, watcher, or harness tools.

### v020-stdio-store-recall

- category: `stdio_mcp`
- passed: `true`
- command_or_query: `MCP store/recall query: clean room adoption handoff v020-clean-room-stdio-token`
- expected_behavior: `store` writes one demo memory and `recall` finds the same record with stdio provenance.
- failure_reason: `none`
- non_goal_guard: Does not count direct MemoryStore calls as MCP round-trip evidence.

### v020-first-run-cli

- category: `cli_report`
- passed: `true`
- command_or_query: `python -m agent_mem_bridge first-run --client generic --namespace project:v020-clean-room --query clean room adoption handoff --example --format json`
- expected_behavior: `first-run --format json --example` parses, stays manual-copy-only, and includes Task Brief.
- failure_reason: `none`
- non_goal_guard: Does not write client config or claim runtime-specific plugin support.

### v020-task-brief-cli

- category: `cli_report`
- passed: `true`
- command_or_query: `python -m agent_mem_bridge task-brief --namespace project:v020-clean-room --query clean room adoption handoff --format json`
- expected_behavior: `task-brief --format json` parses and includes used/review sections from the temp store.
- failure_reason: `none`
- non_goal_guard: Does not mutate memory, promote records, or require AMH.

### v020-isolation-write-scope

- category: `isolation`
- passed: `true`
- command_or_query: `inspect temp DB/config boundaries`
- expected_behavior: No client config file, no live DB mutation, no non-demo durable writeback.
- failure_reason: `none`
- non_goal_guard: Does not touch user/client config, live AMB home, watcher, scheduler, or AMH runtime.

## Stdio MCP Evidence

- entrypoint: `python -m agent_mem_bridge`
- tool_count: `10`
- store_recall_round_trip: `true`
- recalled_client_transports: `stdio`

## CLI Report Evidence

- first_run_schema: `memory.first_run.v1`
- first_run_write_mode: `manual_copy_only`
- task_brief_schema: `memory.task_brief.v1`
- task_brief_used_count: `1`
- task_brief_needs_review_count: `0`

## Boundary

- Local reproducible proof only.
- No vendor-client certification claim.
- No new public MCP tools.
- No client config writes.
- No AMH dependency.
- No watcher, scheduler, daemon, or runtime loop.
