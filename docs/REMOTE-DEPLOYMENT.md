# Remote authority deployment

Run one official AMB authority on a server/NAS and connect remote clients with
standard MCP Streamable HTTP. The database stays on local storage on that host.
Existing local users can continue running `agent-memory-bridge` or
`agent-memory-bridge serve --transport stdio` without HTTP configuration.

These instructions do not authorize replacing an existing authority or switching
production clients. Rehearse on an isolated copy first; see
[the migration procedure](REMOTE-MIGRATION.md).

## Local HTTP first

```sh
agent-memory-bridge serve --transport streamable-http
agent-memory-bridge doctor --url http://127.0.0.1:8000/mcp --json
agent-memory-bridge verify --url http://127.0.0.1:8000/mcp --json
```

The default listener is loopback. The HTTP profile requires a verifiably local
Linux filesystem for the database. `doctor` without `--url` examines local
paths/configuration; remote `doctor`/`verify` perform read-only readiness and
modern/legacy MCP list/stats round trips. They never start a fallback local
authority or write a diagnostic record to production.

The server uses JSON responses through the SDK's standard Streamable HTTP
implementation. It retains protocol negotiation, including supported legacy
clients. No custom JSON-RPC endpoint or separate set of tool handlers exists.

## Authentication and exposure

For an explicit remote listener, configure a dedicated bearer credential file,
Host allowlist, and Origin allowlist. Tokens are provided through files, never
command-line token arguments or URLs. Use a cryptographically random token,
restrict the file to its owner, and grant it only to trusted clients. A token
grants the whole AMB tool surface, including mutation and export.

The private bearer profile uses MCP SDK verification/authentication middleware
and standard HTTP bearer semantics. It does not implement an OAuth server,
issue tokens, advertise a fictional issuer, or promise interoperability with
clients that require OAuth discovery. Such a client's requirements must be
evaluated explicitly before selecting this deployment mode.

Host and Origin checks prevent unwanted request origins; they are not TLS or
identity. Protect remote traffic with TLS or an authenticated SSH/overlay tunnel.
Do not send bearer credentials in plaintext over an untrusted LAN or the
internet. An authenticated reverse proxy must not leave a reachable bypass to
its backend. Configure the externally presented host/origin explicitly and
avoid blanket wildcards. Requests without an Origin header can be valid native
MCP requests; a supplied invalid Origin is rejected.

Keep one dedicated token per authority, separate from all other services. Rotate
the file and restart the service in an approved maintenance window. Do not log
Authorization headers, tokens, complete request bodies, or raw client metadata.
The HTTP bootstrap filters SDK transport log messages because those messages
can include rejected headers or session IDs. Severity and logger/source location
remain available, while request details and exception bodies are omitted.

## Enterprise HTTP Gateway (Multi-Token Auth, RBAC, and Audit Logging)

Starting in v0.38.0, AMB provides an orthogonal Enterprise Gateway layer for Streamable HTTP deployments. It enables multi-token authentication, role-based access control (RBAC), namespace tenant enclaves, and zero-lock structured JSONL audit streaming.

### 1. Gateway Configuration (`amb_tokens.json` or `AMB_GATEWAY_CONFIG`)

Configure principals, token hashes, roles, and allowed namespace patterns:

```json
{
  "principals": [
    {
      "principal_id": "agent:planner",
      "token_hash": "sha256:c43bdeb6daa16e9dbd038fd9df9306376c51fd576e9cb6bea333d2c80df66036",
      "role": "writer",
      "allowed_namespaces": ["project:agent-memory-bridge", "domain:*"],
      "rate_limit_rpm": 120
    },
    {
      "principal_id": "service:ci-auditor",
      "token_hash": "sha256:2421a9f92eeb674cf1cc61a1862c9a95afbaaede5cba12ab4f32a220d31f987f",
      "role": "reader",
      "allowed_namespaces": ["project:*"],
      "rate_limit_rpm": 300
    },
    {
      "principal_id": "system:orca-orchestrator",
      "token_hash": "sha256:19e678d46b2b6515d840ce9a8edfcc0e9b31931a46325b54b87cfa8e4d5bdc65",
      "role": "orchestrator",
      "allowed_namespaces": ["project:agent-memory-bridge", "project:pipeline-*"],
      "rate_limit_rpm": 600
    }
  ]
}
```

To generate a secure token and its deterministic SHA-256 hash locally:

```bash
python -c "import secrets, hashlib; t = secrets.token_hex(24); print('token:', t); print('token_hash: sha256:' + hashlib.sha256(t.encode()).hexdigest())"
```

Pre-hashed tokens use SHA-256 (`sha256:<64-hex>`). In secure local configurations (mode 0600), plain `token` can also be provided directly, and AMB computes the hash at startup. Non-admin roles must explicitly declare their allowed namespaces; blanket `*` is reserved for administrators.

### 2. Role Permissions Matrix

The 17 public MCP tools are governed across four standard roles:

| Role | Allowed MCP Tools | Strictly Forbidden Tools | Namespace Boundaries |
|---|---|---|---|
| **admin** | All 17 tools | None | All namespaces (`*`) |
| **writer** | `store`, `recall`, `browse`, `annotate`, `revise`, `feedback`, `record_run_event` | `forget`, `promote`, `export`, and orchestration tools | Bound to `allowed_namespaces` |
| **reader** | `recall`, `browse`, `stats`, `get_run` | All mutating tools | Bound to `allowed_namespaces` |
| **orchestrator** | `begin_run`, `record_run_event`, `complete_run`, `get_run`, `claim_signal`, `extend_signal_lease`, `ack_signal`, `recall`, `browse` | `forget`, `promote`, `store`, `stats` | Bound to `allowed_namespaces` |

### 3. Namespace Enclaves & Authoritative Actor Binding

- **Namespace Isolation**: Wildcard patterns (`project:*`, `domain:*`, `*`, exact match) block cross-tenant reads and writes. Out-of-scope calls fail closed with HTTP 403 Forbidden.
- **Actor Binding**: In gateway RBAC mode, caller-declared `actor` metadata is overridden with the verified `principal_id` to eliminate agent identity spoofing.

### 4. Zero-Lock Structured Audit Logging (JSONL)

Every HTTP MCP interaction (successful or blocked by 401/403/429) emits a single-line JSONL audit event to stdout, stderr, or a dedicated log file specified by `AMB_AUDIT_STREAM`. Audit logging is completely isolated from the SQLite database WAL to prevent write contention with core memory operations.

## Docker Compose

The image installs a wheel from official AMB source and runs as UID/GID 10001.
It has no second implementation from the old NAS fork. The command is exec-form;
Docker SIGTERM reaches the server for graceful shutdown.

Use the repository's `compose.yml`. Before an approved deployment, provision a
mode-0600 dedicated token file readable by UID 10001, outside the checkout.
Set `AMB_HTTP_TOKEN_FILE` to its path. Compose fails configuration if it is
missing. The token value is not part of Compose environment or image metadata.

For a local candidate build, set `AMB_SOURCE_REVISION` to the exact clean
candidate SHA, then build the Compose image from that checkout. The image's
`AMB_VERSION` must match installed package metadata. An `unverified` revision
label is development-only and cannot pass release provenance acceptance.
After publication, prefer the exact versioned image digest; no mutable `latest`
tag is required.

Compose publishes port 8000 to **host loopback only**, even though the process
listens on all interfaces inside its private container namespace. Token and
Host/Origin checks are still required. Access remotely through a protected
tunnel/proxy. Changing the host port requires corresponding allowed Host/Origin
values via `AMB_ALLOWED_HOSTS` and `AMB_ALLOWED_ORIGINS`. Never expose the
container with a fresh unauthenticated wildcard listener.

The named `amb-home` volume persists the database, WAL, metadata, and local
receipt secret when the application container is removed and replaced. Do not
run `docker compose down --volumes` on an authority. Container replacement is
not database restore and must not rotate its epoch.

For a bind mount, replace only `amb-home:/data/agent-memory-bridge` with an
explicit operator-owned local host directory. Ensure ownership/permissions
match UID/GID 10001 before startup; the non-root image will not recursively
chown arbitrary host data. Verify the actual host filesystem with `findmnt -T`
and the in-container diagnostics. An innocent-looking path can be NFS/CIFS.
Never use NFS/CIFS Docker volume drivers or mounts for live SQLite/WAL.

The image filesystem is read-only under Compose; `/tmp` is a bounded tmpfs and
the named volume is the writable authority. The optional AMB background service
is not required for basic MCP operation. If configured, run `agent-memory-bridge
service` as a separate container/process using the same image and same local
volume, with the existing service singleton lock. Do not introduce another
authority or a process supervisor. Validate enabled background lanes separately.

## Health, readiness, and failure

`/healthz` indicates the HTTP process is alive. `/readyz` checks the authority
database, expected schema, and public contract without dumping memory or doing
an expensive full integrity check on every request. Docker HEALTHCHECK uses
readiness, not PID existence. Use `db-health`/backup verification for deeper
operator checks, not a frequent liveness loop.

If the canonical server is unavailable, remote clients receive a transport
failure. Do not configure local fallback writers. A write can commit while its
response is lost: retry identical `store` content using existing duplicate-safe
semantics; retain explicit idempotency keys for run/event/outcome operations.
Do not assume every tool is safe to retry blindly.

## Release artifacts

Release publication is owner-gated. The PyPI and GHCR workflows check out the
immutable release-event SHA, verify the tag and package version, and test the
artifacts before publishing. Container acceptance checks version/revision labels,
non-root execution, HTTP parity, container replacement, outage behavior, and
readiness negatives. It does not authorize NAS production cutover.

The release gate also requires a real authority-copy migration/rollback rehearsal
and a two-machine first win. See the [frozen evaluations](evidence/v0.34.0-remote-authority/eval-definitions.json).

## Reproduce candidate acceptance

With the candidate installed in an isolated Python environment, run:

```sh
python scripts/check_http_deployment.py \
  --server-python /private/candidate-venv/bin/python \
  --runtime-dir /private/new-http-acceptance
```

The client interpreter can contain MCP 2.x or supported MCP 1.28.1. The runner
creates a new local authority, checks list/store/recall and exact small/500-entry
exports, then terminates its own child process. It never targets production.
Use a fresh runtime path per SDK run. The fixture deliberately produces an
approximately 17 MB export; this is a transport regression, not a recall benchmark.

For two-machine evidence, use `scripts/check_remote_first_win.py` with `--action
store` on machine A and `--action recall` on machine B, the same `--run-id`, and
the same isolated authority URL through a protected tunnel. Each client needs
only the MCP SDK and a private test-token file. Use `--action absent` for the
wrong-namespace control; after stopping only the test authority, use `--action
unavailable` to prove connection failure. Never run the write probe on production.
