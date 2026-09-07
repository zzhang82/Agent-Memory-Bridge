# ADR-0001: Cross-Machine Authority Topology

**Status:** Proposed
**Date:** 2026-09-07
**Cycle:** v0.33 operationalization
**Baseline:** v0.32.2, schema v12, SHA `28401bd3b18444df3a04269dc029d2051faaf39c`

## Context

Agent Memory Bridge (AMB) users operate across desktop, WSL/Linux, macOS,
NAS, and multiple coding clients. Shared project authority is useful: the
same decisions, constraints, run history, and coordination signals should be
available without a manual export/import loop.

The current product contract is still local-first and single-host:

- SQLite/WAL is the durable authority for memories, signals, runs, outcomes,
  receipts, and dynamic state.
- `service_lock.py` uses kernel-level `fcntl.flock` / `msvcrt.locking`. Those
  locks have no cross-host semantics.
- `filesystem_safety.py` already warns on UNC paths, consumer sync folders,
  and WSL-mounted sync locations. The `hardened-local` profile fails that
  check.
- `TRUST-BOUNDARY.md` disclaims distributed locking, exactly-once
  cross-machine coordination, hosted identity, and remote ACLs.
- `SECURITY.md` warns operators to keep the active WAL database on a local
  filesystem.

This ADR selects a multi-machine direction that preserves SQLite/WAL as the
durable local authority. It does not add remote hosting to AMB core.

## Decision

**Selected: Option C — one canonical authority host plus a remote
gateway/adapter.**

One machine owns SQLite/WAL on a local filesystem. Remote machines reach that
host through an authenticated transport adapter. They never open the authority
database themselves.

Transport, authentication, and the remote shim belong in a separate adapter
or deployment package. They are not part of AMB core in this cycle.

## Options Evaluated

### A. Independent local AMB stores

Currently supported. Each machine has its own SQLite database.

- Pros: no new infrastructure; fully offline; no split-brain risk.
- Cons: no shared project authority; run history and signals fragment.
- Verdict: keep as the default single-machine path. Insufficient as the
  multi-machine shared-authority answer.

### B. Shared SQLite file over NAS / network filesystem

Place the WAL database on NAS, NFS, CIFS, or SMB and open it from multiple
hosts.

Rejected. WAL locking is undefined on network filesystems. `fcntl.flock` does
not protect across hosts. Silent corruption is the failure mode, not a clean
error. AMB already warns against this topology. A NAS may be backup, shadow,
artifact storage, or standby source. It must not become a concurrent SQLite
authority.

### C. Canonical authority host + remote gateway

```text
local coding client
        |
   local MCP/stdio
        |
local shim / connector
        |
 authenticated remote transport
        |
canonical AMB authority host
        |
   SQLite/WAL on local FS
```

- Authority owner: the canonical host. One AMB process holds the service lock
  and owns writes.
- Remote shim: a thin local-stdio MCP adapter. It presents the same 17-tool
  surface, forwards tool calls, and holds no durable authority.
- Verdict: selected default for shared multi-machine authority.

### D. Replace SQLite with PostgreSQL

Deferred. Multi-machine access is an access-topology problem, not evidence
that AMB needs multi-writer server semantics. PostgreSQL would replace the
local-first product promise and rewrite storage, FTS, WAL, schema, lock, and
epoch behavior. Revisit only if a canonical host cannot satisfy a genuine
multi-writer requirement.

## Detailed Properties for Option C

### Failure behavior

| Failure | Behavior |
|---|---|
| Canonical host unreachable | Remote shim returns a transport error. No silent local fallback write. No local cache becomes accidental authority. |
| Canonical AMB process crash | Service lock is released; restart recovers. Remote shims fail until the host is back. |
| Network partition mid-request | Request times out. Partial writes do not reach SQLite. Retry of identical `store` content is duplicate-safe via exact content hash. Run/event/outcome retries are safe only when the caller reuses the existing SHA-256 idempotency-key digest. Ordinary `store` has no request-id idempotency. |
| Canonical disk failure | Use the existing SQLite backup/restore path. Restore rotates the database epoch and invalidates outstanding receipts and CAS tokens. |

### Offline behavior

Remote machines are not offline-capable against the shared authority. That is
intentional. Offline writes into a shared authority without conflict
resolution is a different, harder product.

Independent local stores remain available for genuinely offline work. A later
read-only cache could serve recall with explicit staleness markers; writes
would still fail or queue until the authority host is reachable.

### Authentication and authorization

- Authenticate between the remote shim and the transport listener, not
  between the coding client and AMB core.
- Minimum viable personal deployment: pre-shared key or mutual TLS on a
  trusted LAN or VPN.
- For one operator, authentication is authorization. Per-namespace ACLs stay
  out of AMB core.
- AMB core still treats provenance as declared, not authenticated identity.

### Replay and idempotency

Ordinary `store` retries of the same content are duplicate-safe because
durable memory identity is the exact content hash in a namespace. That is
not request-id idempotency: a lost response followed by a retry of
identical content returns the existing record instead of inserting a
second one, but a different payload is a new write.

Run, event, and outcome paths already store SHA-256 idempotency-key
digests. The adapter must pass those keys through unmodified so a retried
timed-out request cannot create a second write on those paths. A future
adapter may add request identity for `store` if ambiguous non-identical
retries become a product requirement.

### Latency

Local stdio stays the fast path. LAN overhead is acceptable for coding-agent
tool calls. WAN/VPN latency is acceptable for ordinary store/recall, but may
be noticeable for aggressive signal polling.

### Backup, restore, and shadow replicas

Backup and restore remain host-local SQLite operations. A NAS may receive
completed snapshots. A shadow replica is always behind the authority by at
least one backup interval and must never be promoted without a deliberate
restore that rotates the epoch.

### Split-brain prevention

Only one machine has the live SQLite file. Only one AMB process holds the
service lock. There is no automatic failover. Copying the database to a
second host creates a different bridge instance; receipts and epoch-bound
tokens fail across instances.

### Schema ownership and version skew

The canonical host owns schema version and migrations. Remote shims are
stateless forwards of the public MCP tool surface. The adapter protocol
should negotiate versions and fail closed on mismatch.

### Local-first product promise

AMB core still runs locally with no hosted service. SQLite stays on local
filesystem. Network access is opt-in through a separate package. Single-machine
users see no required change.

## Explicit Rejections

1. Shared SQLite/WAL over NAS, NFS, CIFS, or SMB.
2. Adding a public HTTP product surface to AMB core in this cycle.
3. Automatic failover or distributed consensus.
4. Offline write-back caches that require merge/CRDT semantics.
5. PostgreSQL as the default multi-machine answer.

## This Cycle

Required: this ADR plus a reproducible topology proof/design.

Optional later, not shipped here:

- a protocol sketch for forwarding MCP tool calls over an authenticated
  channel
- a local proof fixture that store/recall round-trips through a mock shim
  without changing AMB core
- a louder NAS/network-share refusal if the existing filesystem safety check
  is not already fail-closed enough

No transport code, no deployment, no schema v13, no MCP tool #18.

## Remaining Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Canonical host unavailability blocks remote work | Medium | Fall back to independent local stores; future read-only cache for recall |
| Transport authentication becomes a new attack surface | Medium | Keep it in the adapter package; personal mTLS/PSK first; no public HTTP in core |
| Operators ignore warnings and share WAL over NFS | Medium | Keep and, if needed, strengthen the existing local-filesystem checks |
| WAN polling latency | Low | Accept for current workflows; do not add queues or schedulers to core |
| Version skew between shim and host | Low | Protocol negotiation with hard rejection |

## References

- `docs/ARCHITECTURE.md`
- `docs/AUTHORITY-CONTRACT.md`
- `docs/TRUST-BOUNDARY.md`
- `SECURITY.md`
- SQLite WAL: https://www.sqlite.org/wal.html
- How To Corrupt SQLite: https://www.sqlite.org/howtocorrupt.html
