# Roadmap

This page describes **future direction**, not a release ledger. Current implementation facts belong in [Production Status](PRODUCTION-STATUS.md); the system story belongs in [Architecture](ARCHITECTURE.md); detailed historical evidence remains in the existing release announcements, benchmark snapshots, Git history, and GitHub Releases.

> The sequence below is directional, not a release promise. Work advances only when its authority boundary, validation evidence, and review scope are clear.

| Capability area | Intended direction | Boundary that remains in force |
|---|---|---|
| Repository and documentation stabilization | **Delivered in v0.37.0:** Single canonical source facts in `PRODUCTION-STATUS.md`, 4-tier model standardized in `ARCHITECTURE.md`. | Documentation must not replace implementation or relax public, release, or onboarding contracts. |
| Adoption and installation simplification | **Delivered in v0.37.0:** Standardized host configuration recipes for Cursor, Claude Code, OpenCode, and uvx serve; documented cross-platform onboarding diagnostics. | Setup remains local and explicit; configuration and durable writes remain user-controlled. |
| Controlled Context Compiler activation | Consider clearly scoped, user-approved operational activation of the existing derived-view capability after its use boundary is designed and reviewed. | The compiler remains transient, consumes governed task memory and Dynamic State snapshots, and does not become a second retrieval/ranking system. |
| Knowledge Explorer and observability | **Implemented now:** a local-first, read-only Knowledge Explorer MVP over existing project knowledge. **Possible future Explorer expansion:** additional state, run, outcome, and evidence observability only while remaining derived and non-authoritative. | Views, clustering, and reflection remain derived and non-authoritative; no dashboard becomes a write authority by implication. |
| Controlled memory adaptation | Investigate reviewable adaptation informed by bounded evidence and evaluated outcomes. | No automatic ranking changes, prompt/policy mutation, or durable memory writeback follows from a derived signal alone. |
| Verified skill acquisition | Consider human-governed promotion of evidence-backed lessons only after evidence, evaluation, and review contracts are explicit. | No autonomous skill acquisition, self-trusting reflection, or automatic procedure promotion is implied. |

## Medium-Term Direction

Following the delivery of repository stabilization and adoption simplification in v0.37.0, the immediate priority advances to **controlled activation and observability**: operationalizing the transient Context Compiler after explicit boundary review, and expanding read-only Knowledge Explorer projections over run trajectories and verification evidence.

## Later Product Direction

The implemented Knowledge Explorer MVP is a **read-only, local-first** surface over existing project knowledge. Possible future expansion may add additional state, run, outcome, and evidence observability, but must remain derived and non-authoritative. Its useful first questions are inspectable ones: which records, procedures, states, runs, artifacts, outcomes, and context-selection facts exist; what lineage connects them; and which parts are durable authority versus a derived view. Derived grouping or reflection, if explored later, must be clearly marked as non-authoritative.

Any future adaptation or skill work should come after evidence and evaluation are sufficiently reviewable. A reliable outcome linkage is evidence for human review, not evidence of causality, autonomous learning, or a general productivity claim.

## What This Roadmap Does Not Promise

This roadmap does not promise hosted execution, a scheduler, a queue, MCP Tasks, automatic reranking, automatic prompt/policy evolution, automatic durable writeback, authenticated multi-user authority, or autonomous skill acquisition. Those boundaries remain governed by the current contracts unless a future reviewed change explicitly revises them.

## Historical Context

Historical releases, proof artifacts, and benchmark snapshots remain part of the repository as evidence. They are intentionally not repeated here. Use the [release announcements](.) and [GitHub Releases](https://github.com/zzhang82/Agent-Memory-Bridge/releases) when historical detail is needed.
