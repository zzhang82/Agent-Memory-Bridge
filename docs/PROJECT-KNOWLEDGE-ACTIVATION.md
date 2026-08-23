# Project Knowledge Activation

Project Knowledge Activation is an internal v0.30 product-phase capability. It makes the existing repository bootstrap view reusable for local project context while keeping repository-derived facts separate from governed durable memory.

## Authority Model

Repository knowledge is a **derived, rebuildable, non-authoritative** view. A snapshot may be commit-bound only when Git reports a clean worktree. Dirty worktrees and unavailable Git status fail closed, and their content is not attributed to `HEAD`. Repository facts are never inserted into the authoritative `memories` table.

The existing governed task-memory assembly remains the authority for durable project WHY. The Context Compiler may receive selected repository facts as an additional derived input, but it does not rerun retrieval, rank repository records, or persist prompt/context bodies.

## Local Snapshot Store

Snapshots are stored as canonical JSON under the configured bridge-home repository directory. The store is separate from SQLite, Dynamic State, and episode authority. Writes use a temporary file followed by an atomic same-directory replace, so readers observe either the prior complete document or the new complete document.

Each stored snapshot records a repository identity, identity basis, Git root, optional remote origin, the original bootstrap snapshot, and a store schema label. The snapshot itself retains the repository binding state, exact commit when proven, extractor version, facts, sources, exclusions, and uncertainty records.

## Repository Identity

When an origin remote is available, the sanitized remote is retained as logical grouping metadata, but it is not the local source identity. The `local_repository_source_id` incorporates the canonical local Git root, so two clones or worktrees of the same logical remote have distinct local source IDs and distinct snapshot slots. Repositories without remotes likewise use their canonical local Git root. Moving a local clone changes its local source identity; explicit re-bootstrap/rebind is required, and AMB does not silently follow moved roots.

## Namespace Binding

A namespace can be explicitly bound to one repository identity through `bootstrap-repo <path> --namespace <namespace>`. Repeating the same binding is idempotent. A different repository cannot silently replace an existing binding; an operator must provide `--rebind`. Unbinding is local metadata removal and does not delete snapshots or durable memory.

A missing snapshot, dirty worktree, unavailable status, or changed `HEAD` makes the bound source ineligible for current project truth. The system reports the reason rather than silently presenting stale repository facts. Refresh is explicit: rerun `bootstrap-repo`.

## Context and Inspect

Read-only `inspect` shows repository WHAT in a separate `Repository knowledge (WHAT)` section and governed durable WHY in `Durable project memory (WHY)`. Repository entries retain source path, commit, and `derived_repository` authority. Durable entries retain their existing memory identity and governance explanations. A stale or unavailable repository source is reported as unavailable rather than relabeled as durable memory.

The Context Compiler supports the same separation for transient rendered context. Repository inputs use `source=derived_repository`, carry deterministic value fingerprints, and participate in the existing token budget without changing MCP tools or public schemas.

## Security and Non-Goals

The bootstrap extractor continues to enforce its existing bounded protections: no network access, no external model, symlink-escape protection, secret-like exclusion, binary exclusion, oversized-file exclusion, generated-directory exclusion, and fail-closed provenance. Snapshot persistence stores metadata and bounded extracted facts, not hidden reasoning, transcripts, credentials, or arbitrary unbounded source bodies.

This phase does not add a graph or vector database, a watcher, distributed coordination, automatic learning, automatic durable-memory promotion, ranking adaptation, a browser UI, or an eighteenth MCP tool. Schema v12 and the existing public tool-schema digest remain unchanged.
