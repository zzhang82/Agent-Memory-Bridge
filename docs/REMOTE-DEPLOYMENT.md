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
restrict the file to its owner, and grant it only to trusted clients.

In the static single-token private-bearer profile, that one token grants the
whole AMB tool surface, including mutation and export. This is not true of the
optional gateway/RBAC deployment below: there, each token represents one
configured principal and is limited by its role and `allowed_namespaces`. The
mode follows the selected configuration, not the number of tokens. One token can
still select gateway/RBAC. Local stdio does not use either HTTP profile.

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

In the static single-token profile, keep one dedicated token per authority,
separate from all other services. Rotate the file and restart the service in an
approved maintenance window. A gateway/RBAC deployment instead uses its
configured principals and does not require this one-token-per-authority shape.
Do not log
Authorization headers, tokens, complete request bodies, or raw client metadata.
The HTTP bootstrap filters SDK transport log messages because those messages
can include rejected headers or session IDs. Severity and logger/source location
remain available, while request details and exception bodies are omitted.

## Optional HTTP gateway (per-client tokens, roles and audit logging)

Starting in v0.38.0, AMB provides an optional gateway layer for Streamable HTTP deployments. It adds per-client token authentication, role-based access control (RBAC), namespace limits, and structured JSONL audit lines.

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
| **writer** | `store`, `recall`, `browse`, `annotate`, `revise`, `feedback`, `record_run_event` | `stats`, `forget`, `promote`, `export`, `begin_run`, `get_run`, `complete_run`, `claim_signal`, `extend_signal_lease`, `ack_signal` | Bound to `allowed_namespaces` |
| **reader** | `recall`, `browse`, `stats`, `get_run` | `store`, `forget`, `feedback`, `promote`, `annotate`, `revise`, `export`, `begin_run`, `record_run_event`, `complete_run`, `claim_signal`, `extend_signal_lease`, `ack_signal` | Bound to `allowed_namespaces` |
| **orchestrator** | `begin_run`, `record_run_event`, `complete_run`, `get_run`, `claim_signal`, `extend_signal_lease`, `ack_signal`, `recall`, `browse` | `store`, `stats`, `forget`, `feedback`, `promote`, `annotate`, `revise`, `export` | Bound to `allowed_namespaces` |

The Allowed MCP Tools column is the normative contract and must match
`ROLE_PERMISSIONS`. The Strictly Forbidden Tools column is the explicit
complement shown for readers; it is not a second permission source. `admin`
matches all 17 public tools. This table does not claim that every admin
operation is additionally filtered by `allowed_namespaces`.

### 3. Namespace limits and actor binding

- **Namespace limits**: Wildcard patterns (`project:*`, `domain:*`, `*`, exact match) block cross-namespace reads and writes. Out-of-scope calls fail closed with HTTP 403 Forbidden.
- **Actor Binding**: In gateway RBAC mode, the gateway overrides the top-level `actor` for `store`, `annotate` and `revise`, and `provenance.actor` for `feedback`, with the caller's verified `principal_id`, whatever name the caller sends. A caller can't write under another principal's name. It identifies the configured principal, not the person or model behind it.

### 4. Structured audit logging (JSONL)

A request that completes normally, is rejected before the tool, or raises a caught exception writes one JSONL audit line. A disconnected request is not guaranteed to produce one. Audit writes stay separate from SQLite.

Select the destination with `--audit-stream`, `AGENT_MEMORY_BRIDGE_AUDIT_STREAM`, `AMB_AUDIT_STREAM`, or the gateway configuration's `audit_stream`. The choices are stdout, stderr, or a file path. AMB does not rotate, retain, or protect that file. File permissions, rotation, and retention belong to the operator.

A serialized line always contains these twelve fields: `audit_id`, `timestamp`, `request_id`, `principal_id`, `client_ip`, `transport`, `tool`, `namespace`, `status`, `duration_ms`, `arguments_summary`, and `output_summary`. `client_ip`, `tool`, `namespace`, and either summary may be null. `duration_ms` is rounded to two decimal places. `error_message` is included only when it has a value; it is omitted rather than serialized as null.

`status` is one of `success`, `denied_unauthenticated`, `denied_forbidden`, `denied_rate_limited`, `error`, `tool_error`, or `unknown_truncated`. Those are audit labels, not HTTP status numbers. `denied_unauthenticated` corresponds to HTTP 401, `denied_forbidden` to 403, and `denied_rate_limited` to 429. `tool_error` means the captured tool result reported an error. `unknown_truncated` means the captured response reached at least 4096 bytes and the partial capture cannot establish whether the tool succeeded. `error` covers an HTTP status of 400 or higher, or a caught exception.

`arguments_summary` records lengths or counts for `title`, `content`, `replacement_content`, `query`, `goal`, `tags`, and `tags_any`. It preserves `kind`, `limit`, `event_type`, `outcome`, `id`, `signal_id`, and `to_kind` when present, and preserves at most the first 64 characters of `correlation_id`. `output_summary` records selected response metadata such as `id`, `stored_id`, `status`, `count`, `item_count`, `outcome`, `run_id`, `tools_count`, `content_blocks`, and `is_error`; a truncated capture adds `truncated: true`.

This is field minimization, not redaction. The summarizer does not copy the dedicated memory-content or query-text fields listed above. It does not remove sensitive text that a caller places in a retained identifier, namespace, correlation prefix, or another preserved label. Do not put secrets or memory text in those fields.

### 5. What the gateway does not do

- It does not add a human approval gate.
- It does not provide OAuth, token issuance, or an OAuth discovery server.
- It does not terminate TLS. Put TLS or an authenticated tunnel in front of a remote listener.
- It identifies the configured `principal_id`, not the person or model that supplied the token.
- It does not protect direct local database access or the local stdio path. Those paths do not pass through this gateway.

## Docker Compose

The image installs a wheel from official AMB source and runs as UID/GID 10001.
The command is exec-form;
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
readiness negatives.

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
