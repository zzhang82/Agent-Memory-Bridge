# MCP 2026-07-28 Compatibility Contract

Last updated: 2026-07-29 (America/New_York)

This is the compatibility denominator for AMB 0.26.1. It defines the smallest
compatible runtime contract for the dual-era implementation without widening
the AMB product boundary.

This document is not a release note. Release metadata remains in
`pyproject.toml`.

## Source Basis

Official primary sources:

- [MCP 2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog.md)
- [MCP 2026-07-28 basic protocol](https://modelcontextprotocol.io/specification/2026-07-28/basic.md)
- [MCP 2026-07-28 stdio transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio.md)
- [MCP 2026-07-28 server discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover.md)
- [MCP 2026-07-28 tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools.md)
- [MCP 2025-06-18 lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle.md)
- [MCP 2026-07-28 authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization.md)
- [MCP extensions overview](https://modelcontextprotocol.io/extensions/overview.md)
- [MCP Apps extension](https://modelcontextprotocol.io/extensions/apps/overview.md)
- [MCP Tasks extension](https://modelcontextprotocol.io/extensions/tasks/overview.md)

## Non-Goals

AMB 0.26.1 compatibility work must explicitly exclude:

- HTTP hosting or Streamable HTTP service exposure
- MCP Tasks
- MCP Apps
- Episode Ledger, branch comparison, or training export scope
- new public MCP tools
- OAuth, login, hosted identity, or per-namespace ACL enforcement
- schema v8 or any database migration

Any need for one of these items is a separate design review, not part of the
MCP 2026-07-28 compatibility denominator.

## Compatibility Baseline

The compatibility baseline inherited from AMB 0.25.2 is:

- local stdio MCP server
- SQLite/WAL as durable authority
- schema version 7
- exact public MCP tool surface of 13 tools
- caller-declared provenance, not authenticated identity
- retrieval feedback as append-only shadow evidence
- Task Briefs, activation receipts, review queues, and context assembly as CLI
  or derived reports, not public MCP tools

The 13 public tools are:

1. `store`
2. `recall`
3. `browse`
4. `stats`
5. `forget`
6. `feedback`
7. `promote`
8. `annotate`
9. `revise`
10. `export`
11. `claim_signal`
12. `extend_signal_lease`
13. `ack_signal`

## Dual-Era Stdio Contract

AMB 0.26.1 supports both protocol eras over stdio:

- modern MCP 2026-07-28 clients using `server/discover` plus per-request
  `_meta`
- legacy initialize-based clients using `initialize` followed by
  `notifications/initialized`

Modern clients should be able to send `server/discover` before any other
request. AMB should return a `DiscoverResult` with:

- `resultType: "complete"`
- `supportedVersions` including `2026-07-28`
- `capabilities.tools`
- `_meta["io.modelcontextprotocol/serverInfo"]`
- `ttlMs: 300000`
- `cacheScope: "public"`

Legacy clients that do not use `server/discover` must still be able to complete
the existing initialize-based stdio flow. The legacy path must not require
modern per-request `_meta`, must not require clients to parse `resultType`, and
must preserve the current 13-tool surface.

For client-side compatibility probes, follow the official stdio fallback model:

- `server/discover` returning a `DiscoverResult` means the server is modern.
- `UnsupportedProtocolVersionError` means the server is modern but the requested
  version is not supported; choose from the advertised supported list and do not
  fall back to `initialize`.
- any other error or reasonable timeout means the server may be legacy; fall
  back to `initialize`.
- fallback must not key on one hard-coded JSON-RPC error code.

## Result And List Contract

All modern MCP 2026-07-28 results emitted by AMB should include
`resultType: "complete"` unless a future reviewed protocol feature intentionally
adds another result type. AMB 0.26.1 must not implement multi-round-trip input,
Tasks, or `input_required` behavior.

For `tools/list`, AMB must return:

- exactly the 13 public tools listed in this document
- no hidden Task Brief, activation receipt, review queue, Apps, Tasks, OAuth, or
  Episode Ledger tools
- deterministic ordering when the underlying tool set has not changed
- `resultType: "complete"`
- `ttlMs: 0`
- `cacheScope: "private"`

Because AMB 0.26.1 excludes OAuth and per-request ACLs, the tool list must not vary
by connection, prior request side effects, or caller-declared metadata.

The order in "Compatibility Baseline" is canonical. Runtime emission, tests,
operator probes, and release documentation must agree with
`mcp_boundary.PUBLIC_TOOL_ORDER`; a registration mismatch fails closed.

## Metadata Trust Boundary

MCP 2026-07-28 moves protocol version, client information, and client
capabilities into per-request `_meta`. AMB may use these fields for protocol-era
handling and logging, but must not treat them as authenticated identity.

Bounded observability may record client name, client version, protocol version,
a hashed request identifier, a capabilities digest, and a valid trace ID. It
must not log or persist raw capabilities, baggage, request IDs, tokens, or
unbounded nested metadata.

`_meta["io.modelcontextprotocol/clientInfo"]`, AMB `source_client`,
`source_model`, `client_session_id`, `client_workspace`, `client_transport`,
`source_app`, and `actor` remain caller-declared metadata. They can support
filtering, debugging, receipts, and audit review. They are not proof of:

- external client origin
- model execution
- user identity
- workspace ownership
- authorization
- vendor certification

No caller-supplied `_meta`, provenance field, or tag may bypass reserved
governance checks, mutate the public tool surface, create additional feedback
votes for the same receipt-bound subject, or change recall ranking.

For the broader boundary, keep
[TRUST-BOUNDARY.md](TRUST-BOUNDARY.md),
[CLIENT-PROVENANCE.md](CLIENT-PROVENANCE.md), and
[AUTHORITY-CONTRACT.md](AUTHORITY-CONTRACT.md) authoritative.

## Schema And Migration Posture

AMB 0.26.1 MCP compatibility is a transport/protocol adaptation. It must not
change durable storage.

- Keep `CURRENT_SCHEMA_VERSION = 7`.
- Do not add schema v8.
- Do not add tables, columns, indexes, triggers, or migration ledger entries.
- Do not persist raw per-request MCP `_meta` by default.
- Do not change memory, Signal, feedback, receipt, or projection row semantics.
- Do not alter ranking, FTS, embedding, or feedback-effective-vote behavior.

If a future implementation requires durable storage changes, that is out of
scope for this denominator and requires a separate migration plan with rollback
tests.

## Rollback Posture

Compatibility code should be isolated enough that rollback restores the existing
legacy stdio behavior without database repair.

Minimum rollback expectations:

- a failed modern `server/discover` path must not corrupt or create durable rows
- protocol negotiation state must not be stored in SQLite
- disabling the modern adapter should leave initialize-based clients usable
- no database restore should be required because there is no schema v8
- release gates must fail closed if the public tool count differs from 13
- if deterministic list ordering regresses, roll back the protocol adapter rather
  than changing the documented tool surface

## Compatibility Matrix

| Client era | AMB era | Expected path | Expected result |
| --- | --- | --- | --- |
| Python MCP 2.0.0 | AMB 0.26.1 | `server/discover`, then per-request `_meta` | modern complete results; discover `300000/public`; deterministic 13-tool list `0/private` |
| TypeScript MCP client 2.0.0 | AMB 0.26.1 | auto version negotiation | discover, list, store, and recall succeed over spawned stdio |
| Python MCP 1.28.1 | AMB 0.26.1 | `initialize`, then `notifications/initialized` | initialize, list, store, and recall succeed from a separate client environment |
| dual-era MCP 2026-07-28 stdio | AMB 0.25.x | `server/discover` fails or times out, then legacy fallback | existing initialize-based stdio path; no modern support claim |
| initialize-based legacy stdio | AMB 0.25.x | existing path | existing behavior |
| HTTP or hosted client | any AMB 0.26.1 denominator build | out of scope | no HTTP hosting, OAuth, or remote identity claim |
| Apps or Tasks client | any AMB 0.26.1 denominator build | out of scope | no Apps, Tasks, `input_required`, or task handle contract |

## Implementation Checklist

Before code changes are accepted for AMB 0.26.1 compatibility:

- `server/discover` works before initialize on stdio.
- Legacy initialize still works.
- Modern requests accept protocol `_meta` without trusting it as identity.
- Modern results include `resultType: "complete"`.
- Modern discover includes `ttlMs: 300000` and `cacheScope: "public"`.
- Modern `tools/list` includes `ttlMs: 0`, `cacheScope: "private"`, and the
  canonical ordering.
- The emitted public tool set is exactly the 13 names listed above.
- No new MCP tools are added.
- `feedback` remains shadow-only and does not affect ranking or memory rows.
- `CURRENT_SCHEMA_VERSION` remains 7.
- No schema migration is introduced.
- Raw-wire tests cover modern discovery/list/call, legacy
  initialize/initialized/list/call, missing envelopes, malformed client info,
  unsupported-version behavior, modern result fields, and legacy field
  isolation.
- Separate environments cover Python MCP 1.28.1 and Python MCP 2.0.0.
- The official TypeScript MCP client 2.0.0 covers modern discover/list/call.
- `doctor` and `verify` report both eras independently against isolated stores.
- A 20-process shared-SQLite proof and 100 connect/disconnect cycles complete
  without lock errors, remaining direct child processes, or temp-artifact
  leakage.

## Validation Commands

Run these from the repository root after implementation:

```bash
python -m agent_mem_bridge verify --json
python -m agent_mem_bridge doctor --include-stdio --json
python -m pytest tests/test_mcp_boundary.py tests/test_mcp_raw_wire.py tests/test_v026_dual_era.py
python scripts/run_mcp_reliability_proof.py --writers 20 --cycles 100
python ./scripts/check_public_surface.py
python ./scripts/check_onboarding_contract.py
python ./scripts/check_release_contract.py
```

For this document, validate the contract text and official links:

```bash
rg -n "server/discover|initialize|resultType|ttlMs|cacheScope|feedback|CURRENT_SCHEMA_VERSION|schema v8|HTTP hosting|Tasks|Apps|OAuth|Episode Ledger" AGENTS.md docs/MCP-2026-COMPATIBILITY.md
```

```bash
for url in \
  https://modelcontextprotocol.io/specification/2026-07-28/changelog.md \
  https://modelcontextprotocol.io/specification/2026-07-28/basic.md \
  https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio.md \
  https://modelcontextprotocol.io/specification/2026-07-28/server/discover.md \
  https://modelcontextprotocol.io/specification/2026-07-28/server/tools.md \
  https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle.md \
  https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization.md \
  https://modelcontextprotocol.io/extensions/overview.md \
  https://modelcontextprotocol.io/extensions/apps/overview.md \
  https://modelcontextprotocol.io/extensions/tasks/overview.md; do
  curl -L -s -o /dev/null -w "%{http_code} %{url_effective}\n" "$url"
done
```
