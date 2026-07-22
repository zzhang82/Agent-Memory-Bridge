# Trust Boundary

Agent Memory Bridge is a local-first MCP server for reusable engineering memory
and lightweight coordination. It is built for trusted local operators who want
inspectable memory, not for hosted identity, multi-tenant authorization, or
remote access control.

## What AMB Provides

- a local SQLite-backed memory and signal store
- explicit namespaces, tags, provenance fields, and record metadata for
  filtering and review
- optional local operating profiles that tighten cooperative behavior
- raw export paths for inspection, backup, migration, and debugging
- bounded local command-provider controls for classifier and embedding helpers

## What AMB Does Not Provide

- authenticated client, user, model, or workspace identity
- OAuth, login, hosted identity, or per-namespace ACL enforcement
- multi-user or remote ACL systems
- sandboxing for configured local command providers
- distributed locking or exactly-once coordination across machines
- compliance certification or regulated-data handling guarantees

## Local Operating Profiles

`local-single-user` is the compatibility profile for one trusted local operator.
`hardened-local` requires Signal claim-before-ack behavior and rejects trusted
shell command providers.

Both are cooperative local governance profiles. They help a disciplined local
operator avoid accidental misuse, but they do not authenticate callers, isolate
users, enforce namespace permissions, sandbox helper commands, or protect a live
database shared across remote machines.

## Declared Provenance And Tags

Fields such as `source_client`, `source_model`, `client_session_id`,
`client_workspace`, and `client_transport` are declared provenance. They may come
from a tool call or from local launcher defaults.

AMB stores those values so records are easier to filter, debug, compare, and
audit. They are not proof that a specific client, model, user, workspace, or
vendor produced the record.

Caller-supplied tags are also declared labels. They improve retrieval and review,
but they are not authenticated authority. Security-sensitive or governance
behavior must validate policy at the bridge boundary rather than trusting a tag
because a caller supplied it.

## Exports Are Sensitive

Exports are readable snapshots of stored records and metadata. They can include
raw memory content, namespaces, tags, timestamps, local paths, sessions,
workspace labels, model labels, and caller-declared provenance.

Treat exports as sensitive project memory. Sanitize or replace private values
before sharing them in public issues, discussions, pull requests, reports, or
benchmarks.

## Contributor Rule

Public docs and examples should preserve this boundary. Do not describe AMB as
an identity system, ACL system, hosted memory platform, OAuth integration,
compliance layer, or remote multi-user backend unless that work is explicitly
scoped and implemented.
