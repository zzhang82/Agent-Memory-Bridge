# Session-Start Memory Index (#96)

`agent-memory-bridge compile-index` generates an operator-visible, compact Markdown index of current confirmed durable records for session-start orientation.

```bash
agent-memory-bridge compile-index
agent-memory-bridge compile-index --namespace project:example
agent-memory-bridge compile-index --out MEMORY-INDEX.md
```

## Architectural Invariants

1. **Pure Derived Projection ($Index = \pi(Durable)$)**:
   The index holds zero durable authority. It is completely regenerable from SQLite at any time. Deleting or modifying the index file does not alter durable memory rows.

2. **Strict Size Ceiling (<= 200 lines and <= 25 KB)**:
   Long instruction files degrade model attention and instruction adherence. The compiled index enforces a strict ceiling of at most 200 lines and at most 25,600 bytes. Records are prioritized by authority class (`decision`, `constraint`, `gotcha`), listing identifiers, titles, and domain pointers without dumping raw prose.

3. **Documented Fail-Open Path**:
   If the compiled index file is missing, corrupted, or unreadable, connecting clients fail open immediately. The bridge's core targeted `recall` continues unaffected.

4. **Zero MCP Surface Expansion**:
   The index compiler is a local CLI utility (`compile-index` and `memory compile-index`). It does not add an 18th MCP tool or alter the frozen 17-tool MCP schema digest.

5. **Complements Targeted Recall**:
   The index provides an overview of knowledge boundaries (solving "unknown unknowns"). When an agent encounters a specific task, it uses targeted `recall` to retrieve full context and relations.
