# Candidate review ledger

This records implementation judgments, not independent release approval.
Actual eval outputs, private snapshots and host observations remain outside the
repository. The frozen E1-E12 criteria still govern release readiness.

| Area | Current judgment | Largest plausible failure | Challenged assumption and reversal fact | Simpler adequate alternative | Scope delta and gap |
| --- | --- | --- | --- | --- | --- |
| Transport/security | Reuse the existing MCP server through SDK Streamable HTTP, with explicit private bearer configuration. | A rejected request leaks credentials or reaches a tool without the intended boundary. | SDK transport logs include raw headers/session IDs, so HTTP bootstrap filters those logs. A supported client requiring OAuth discovery would reopen the authorization decision. | Direct SDK HTTP; no shim or OAuth server unless a target requires it. | Optional bootstrap only; 17 tools/schema/digest unchanged. Independent adversarial review remains required. |
| Container/operations | One installed-wheel image, non-root process and host-local persistent volume. | Wrong volume attachment creates an empty authority or a remote mount hides behind a normal path. | A Dockerfile is not persistence evidence. Replacement, different-volume and readiness-negative probes are required. A failing replacement probe reverses acceptance. | One process per container; background service optional. | No supervisor or second implementation. Optional background lanes must be validated if enabled. |
| Authority/migration | Strict snapshot validation and copy-only rehearsal preserve durable state and epoch semantics. | Existing metadata issues are normalized or ignored to manufacture a migration pass. | SQLite integrity and equal row counts are insufficient. Content hashes, indexes, recall/write and rollback evidence are required. Source full-verification failure prevents migration acceptance. | Existing SQLite backup/restore APIs and isolated manifests. | No schema or durable-memory rewrite. |
| Release integration | Build/test immutable source and retain every unavailable/failed probe. | Green unit tests obscure missing migration, cross-machine, or independent evidence. | Missing evidence is not PASS. Any unrun critical evaluation prevents release readiness. | Existing doctor/verify, CI and release workflows extended in place. | Publication/tag/cutover remain owner-gated; no claim of a published v0.34 artifact. |

## Explicit challenge answers

The strongest reason not to ship is an unclosed actual-source migration gate,
even if installed-package and container tests pass. The candidate does not
contain a second AMB product implementation: HTTP calls the same MCP factory.
Local stdio remains unchanged and does not auto-switch to a proxy.

Two writable copies are permissible only as clearly isolated rehearsal fixtures.
They must never attract production clients. Cutover must stop the old authority
before the final backup and new authority startup. A write may commit despite a
lost response; clients must not infer failure or retry every tool blindly.

Host/Origin and bearer checks are enforced in code. TLS/tunnel provisioning is
an external operator boundary that needs observed deployment evidence; words in
this ledger do not establish that boundary. Known network filesystems and
unknown classifications cannot satisfy hardened HTTP authority startup.

Deferred scope remains: stdio-to-HTTP shim, full OAuth integration, image
signing/SBOM, and all excluded memory/schema/distributed-writer redesigns.
