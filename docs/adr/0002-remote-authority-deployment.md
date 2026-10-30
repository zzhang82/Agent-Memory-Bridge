# ADR-0002: Optional standard remote deployment

Status: accepted for v0.34.

V0.34 implements ADR-0001's single canonical authority topology inside the
official package as an optional deployment surface. It supersedes only that
ADR's cycle-specific deferral of HTTP and its assumption that every remote
client needs a local shim. Direct SDK Streamable HTTP is the deployed transport;
stdio remains the local default. Tool handlers and storage semantics are shared.

The authority host keeps SQLite/WAL on a verified local filesystem. Strict
deployment startup rejects known remote and unknown filesystem classifications.
No client opens network-mounted WAL or silently falls back to another writer.

The private deployment profile composes the SDK's bearer TokenVerifier,
authentication backend, and authorization middleware without inventing OAuth
issuer metadata. A dedicated pre-provisioned token authenticates whole-authority
access. TLS, authenticated tunnels, and token provisioning are operator-owned;
full OAuth authorization interoperability is a separately evidenced requirement.
No first-party authorization server is added.

One reusable non-root image installs the official wheel. A persistent local
volume holds the authority. The optional background service remains a separate
process/container when needed; no supervisor or second implementation is added.

Migration proceeds through a consistent immutable backup, isolated rehearsal,
state and behavior comparison, rollback rehearsal, and owner-approved cutover.
A lost response can follow a successful commit; existing content deduplication
and run idempotency contracts apply, not exactly-once network guarantees.

Deferred: local stdio shim without a demonstrated target-client need, OAuth
server hosting, PostgreSQL, distributed writers, automatic failover, schema v13,
automatic learning, and tool 18. Independent local stores are separate explicit
workflows, never an automatic response to canonical-authority failure.
