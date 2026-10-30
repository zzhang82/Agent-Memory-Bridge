# v0.34.0: Remote Authority Deployment

Edited after release to remove an outdated, release-specific classification. The later evaluation-definition edit is described under Frozen evaluations and status discipline.

## Frozen scope

Productize optional remote deployment in the official AMB package. One authority
host owns one SQLite/WAL database on its local filesystem. Remote clients use
standard MCP Streamable HTTP; local stdio remains the default. Success requires
installed-package, container, migration, and independent-machine observations.
Unit tests alone cannot establish release readiness.

Baseline: release 0.33.0, tag `v0.33`, commit
`cb314ebbf96d48c13c0c298f6ac7273d701682f4`. The source `main` and remote `main`
matched that commit at inspection. The v0.33.1 semantic-policy experiment is not
part of this release. Do not import that branch wholesale.

Invariants:

- Schema v12, exactly 17 public MCP tools, and public tool-schema SHA-256
  `24c5c52321d61b4b6f647c0d74e2d8304ca68716c403e08a274e9badfd8dc9f8`.
- SQLite/WAL durable authority; indexes, exports, and projections remain derived.
- No automatic learning, new memory semantics, local fallback writer, or shared
  network WAL. Existing local stdio clients require no migration.
- MCP SDK policy `mcp>=2.0.0,<3`; a floor change requires observed necessity and
  explicit compatibility documentation, not convenience.

## Required deliverables

| ID | Acceptance obligation |
| --- | --- |
| D1 | Official CLI serve path, SDK Streamable HTTP, same server/handlers, modern and supported legacy clients, stdio regression. |
| D2 | Official Dockerfile and Compose, persistent local volume, non-root service, deterministic command, graceful termination, meaningful healthcheck, version/revision labels, bind/named-volume guidance, gated GHCR workflow. |
| D3 | Loopback default, explicit remote bind/hosts/origins, Origin protection, safe exposure boundary, no secret logs, documented TLS/auth boundary. No custom authentication protocol. |
| D4 | One logical authority/database on a local filesystem; transport failure when unavailable, never silent local fallback writes. |
| D5 | Review existing deployment changes before porting; immutable backup, isolated real-data rehearsal, state/behavior reconciliation, rehearsed rollback, owner-gated production upgrade. |
| F1 | Actual mounted-filesystem classification, known network FS rejection in hardened deployment, actionable ordinary-mode diagnostics, unknown remains unknown, deterministic fixtures. |
| F2 | Extend existing doctor/verify for transport, exposure settings, DB/FS, package/schema/tool digest, HTTP reachability/list/call, optional service health. |
| F3 | Minimal non-MCP liveness/readiness without contents; readiness checks DB/schema/contract/authority, no recurring expensive integrity scan. |
| F4 | Small and deliberately large export on modern and supported legacy HTTP; repair only a reproduced supported-path defect. |

## Optional ledger

S1 stdio-to-HTTP shim: deferred unless a target client needs it.
S2 SDK OAuth resource server: bounded evaluation; external auth server,
authenticated reverse proxy, or trusted overlay may satisfy v0.34. A first-party
OAuth server is not required. A target client's interoperability requirement is
the only reason to promote this scope.
S3 signing/SBOM: optional if simple; does not block functional deployment.

Excluded: PostgreSQL, schema v13, tool 18, distributed writers/failover,
offline merging, semantic triggers, automatic learning, hosted control plane,
namespace ACL platform, process supervisor without a demonstrated need.

## Team and evidence ownership

R1 owns transport/bootstrap/security and HTTP tests. R2 owns Docker/Compose,
lifecycle/persistence acceptance, and deployment guidance. R3 owns filesystem
classification, snapshots, migration, and rollback.
R4 integrates CLI/diagnostics, version/contracts/docs/CI, evaluates the combined
candidate, and owns release judgment. Shared-file edits have one owner.

For each specialty retain: judgment, largest plausible failure, challenged
assumption, fact that would reverse the recommendation, simpler adequate route,
scope delta, and capability/evidence gaps. Agreement is not product evidence.

Work samples: WS-TRANSPORT, WS-DOCKER, WS-MIGRATION, WS-CROSS-MACHINE.
Each needs inspectable artifacts and externally observable behavior. A fresh
independent reviewer must rerun critical probes against the frozen candidate.

## Migration boundary

Classify every inspected deployment change before porting it. Keep private paths, credentials, snapshots, and machine-specific reports outside tracked source. Track sanitized summaries and reproducible procedures only.

Old production remains active during rehearsal. Obtain a consistent immutable
SQLite backup, then create an isolated rehearsal copy. Compare schema, epoch,
durable table contents, namespaces, FTS, embeddings, recall, writes, runs, and
signals. Rehearse rollback using copies. Copying a running DB without its WAL is
not a backup. A clone is not production authority and must not attract clients.

Only after owner cutover approval: stop the old authority, take the final backup,
start the official authority, switch clients, reconcile, and retain rollback
until reconciliation passes. Lost response is not proof of a failed commit;
preserve existing duplicate/idempotency semantics and receipt/epoch guards.

## Frozen evaluations and status discipline

`eval-definitions.json` is frozen before implementation and final evaluation.
Record its SHA-256 with evidence. Any correction to a defective probe must keep
the original result and explicitly explain the correction. Never weaken a
criterion to obtain a pass.

After freezing, one environment-specific term in the E9 PASS condition was
redacted to generic wording. The pass criterion still means a consistent copy
of the actual source authority. The original frozen file, and the SHA-256 that
matches it, stay in Git history at tag `v0.34.0`. This contract does not record
that digest. The current file's SHA-256 is
`d9bc928a092f392d49b10c8669b7c0c0e3961d1bd30518835015b01edb5e1a45`, which does
not match the file at that tag
(`8c2fa505e9b2fde918713769859c81a4bec9fdbf2235d17f8ad21cba2188cf79`).

PASS requires observed adequate-good and negative/control evidence. FAIL means
the product violated the criterion. Missing runtime/source evidence means
INCONCLUSIVE. N/A requires an explicit genuine scope reason, not inconvenience.
No critical eval may be merely unrun when proposing release readiness.

Final report includes candidate branch/SHA/version; D1-D5/F1-F4 evidence;
E1-E12 statuses/paths; four work samples; optional ledger;
remaining risks; and exact owner actions. Candidate artifact provenance can be
verified before publication; a nonexistent release tag/publication must remain
unobserved, not falsely reported as equal to a tested candidate.

## Approval gates

Authorized: inspect, edit, test, local image builds, isolated rehearsal,
branches/commits, and PR-ready artifacts.

Not authorized by this contract: merge to main, release tags, GitHub Release,
PyPI/GHCR publication, stopping production, replacing the production database,
switching production clients, or upgrading an existing deployment. Review verdicts do not grant those
permissions. No cleanup of old worktrees, production artifacts, or workarounds
without separate approval.

## Continuation packet

Objective: v0.34 candidate, not publication or cutover.
Last known state: v0.33 baseline verified; no runtime changes at scope freeze.
Durable context: AMB core/team/workflow/skill/workspace and project memory
recalled; current files and commands outrank historical implementation claims.
Open threads: target-runtime availability, security boundary,
all implementation and live eval evidence.
Gotchas: isolate AMB home for probes; preserve user-owned worktrees; avoid
cross-platform virtual-environment replacement and generated snapshot churn.
Next actions: complete fork/SDK inspection; implement bounded transport/container/
filesystem slices; integrate diagnostics and run frozen evals.
Do not repeat: v0.33.1 semantic-policy implementation or v0.33 release work.
Context confidence: high for baseline/contract; deployment availability unverified.
