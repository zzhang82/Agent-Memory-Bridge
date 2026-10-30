# Memory Hygiene: Structure Over Prose

Last updated: 2026-10-28

## Purpose

As coding agents run in longer multi-turn workflows, Agent Memory Bridge (AMB) serves as the persistent memory substrate. However, production agent loops reveal two recurring operational hazards:

1. **Memory Pollution**: Treating memory as a prompt trash can for syntax warnings, directory conventions, and linter errors.
2. **Intermediate Scaffolding Overbuild**: Sandwiching complex wrapper daemons, packet previewers, or separate proof harnesses between the executor and AMB.

This guide defines the engineering boundary: **Encode lessons in structure over prose**.

---

## 1. The Core Principle: Structure Over Prose

> If an agent makes the same mechanical mistake twice, harden the repository environment—do not write more prose to memory.

Mechanical rules written as prompt text are non-deterministic, cost tokens on every recall, and dilute context attention. AST linters, typecheckers, and pre-commit hooks reject invalid code deterministically at zero token cost.

### The Boundary Split: WHAT vs WHY

| Dimension | Code & Environment (The WHAT) | AMB Durable Memory (The WHY) |
|---|---|---|
| **Enforcement** | Deterministic (Compiler, Linter, CI, AST) | Contextual (LLM reasoning and planning) |
| **Examples** | ESLint/Biome/Ruff rules, TypeScript type narrowing, folder layout conventions, pre-commit hooks, unit tests | Architectural decisions, business invariant boundaries, third-party library gotchas, security boundaries |
| **Failure Mode** | Build or lint failure with exact line numbers | Subtle regressions, incorrect architectural assumptions, wasted research |
| **Token Cost** | Zero runtime tokens | Incurred on recall |

---

## 2. The 3-Question Ingestion Filter

Before calling `store` or `revise` on durable memory (`kind="memory"`), evaluate these three questions in order:

1. **Can this mistake be caught by a linter rule?**
   - *Action*: Configure the linter (ESLint, Biome, Ruff). Do **not** store it in AMB.
2. **Can this invariant be enforced by types or runtime assertions?**
   - *Action*: Harden the type system (TypeScript branded types, type guards, Pydantic validators). Do **not** store it in AMB.
3. **Is this an irreversible architectural decision, external library gotcha, or non-derivable domain fact?**
   - *Action*: Store it in AMB with explicit context, rationale, and trigger.

---

## 3. Two Operational Anti-Patterns

### Anti-Pattern 1: The Memory Pollution Anti-Pattern

When an agent fails a lint rule, imports a module from the wrong directory, or uses an outdated API method, the naive reaction is to store an instruction:

> *Negative Example*: "Remember not to import from `src/legacy/` in new components."

**Why this fails**:
- Floods the namespace with micro-rules.
- Degrades full-text search (FTS) and retrieval precision.
- Consumes context window without providing deterministic prevention.
- LLMs frequently overlook negative prompt constraints under high token load.

**The Fix**:
Add an ESLint/Biome `no-restricted-imports` rule or an import boundary in the build tool. The linter catches violations immediately at compile time.

### Anti-Pattern 2: The Intermediate Scaffolding Anti-Pattern

Teams often attempt to govern agent memory by building complex intermediate wrappers, external CLI daemons, local packet compilers, or secondary caching proxies between the coding agent and AMB.

**Why this fails**:
- Introduces directory-sensitivity issues and synchronization drift.
- Adds unnecessary latency and failure modes to the agent's inner loop.
- Couples agent tooling to custom host wrappers that break across updates.

**The Fix**:
Connect agent hosts (Cursor, Codex, OpenCode, Claude Code, Hermes) directly to AMB via native MCP (Stdio or Streamable HTTP). AMB already handles atomic SQLite storage, compare-and-swap (CAS) concurrency, versioned supersession (`revise`), and clean separation between durable knowledge (`kind="memory"`) and transient coordination (`kind="signal"`).

---

## 4. What Belongs in AMB vs Code

| Category | Route to Code / Environment | Route to AMB Durable Memory |
|---|---|---|
| **Code Formatting & Style** | Ruff / Prettier / Biome formatters | *Never* |
| **Import & File Organization** | Linter rules, path aliases, tsconfig | *Never* |
| **Type Invariants** | Discriminated unions, type narrowing | *Never* |
| **Third-Party Gotchas** | *Cannot be caught by local compiler* | Undocumented library behaviors, upstream bugs, SDK workarounds |
| **Architectural Trade-offs** | Implementation diff only | Why Option A was chosen over Option B, rejected alternatives |
| **Business Invariants** | Assertions & tests where feasible | Non-derivable domain boundaries, legal constraints, contract rules |
| **Operational Coordination** | *Not applicable* | Signals for handoff, review requests, lease-based coordination |

---

## 5. Lean Memory Templates

When storing durable knowledge, write concise claims with explicit triggers and boundaries rather than freeform conversational summaries.

### Decision Record

Use when capturing an architectural choice that the code AST cannot explain.

```yaml
record_type: decision
claim: Use native MCP Stdio directly instead of intermediate CLI wrapper daemons.
trigger: Evaluating agent integration architecture.
rationale: AMB provides atomic SQLite durability and CAS concurrency directly; intermediate wrappers add maintenance overhead and directory drift.
boundary: Applies to all client integrations; does not prevent host-level MCP launcher config.
```

### Gotcha Record

Use when documenting an external failure mode or non-obvious pitfall.

```yaml
record_type: gotcha
claim: Python 3.12 sqlite3 requires explicit timeout on Windows during concurrent WAL access.
symptom: DatabaseLockedError raised during parallel run worker completion.
cause: Default Windows file locking semantics under concurrent WAL checkpoints.
fix: Set PRAGMA busy_timeout = 5000 in connection factory.
```

## Dependency re-check

Use `depends_on: <memory-id>` when a decision relies on another AMB record. Use `depends_on_repo: <path>@<commit>` when it relies on a file at a clean Git commit. Recall may return `recheck_status: needs_recheck` with a reason. A dirty, missing, or unavailable repository returns `unknown`, not `current`.

The flag does not hide, revise, or automatically update the decision. Clear it deliberately with `revise` or confirm it with `annotate`. Do not store a dependency merely to repeat a fact that the current repository already states.

### Learn Record

Use when capturing a reusable engineering rule or verified constraint.

```yaml
record_type: learn
claim: Keep public MCP tool surface frozen at 17 tools; derived views belong in CLI.
trigger: Proposing new inspection or exploration features.
rationale: Broadening tool count increases tool-selection ambiguity for client LLMs without improving capability.
```
