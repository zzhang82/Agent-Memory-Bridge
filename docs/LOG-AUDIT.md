# Operator Log Audit (#97)

`agent-memory-bridge audit-logs` provides an offline review pass over operator-supplied session transcripts (JSONL or plain text) to stage candidate gotchas and decisions into the review queue.

```bash
agent-memory-bridge audit-logs path/to/session.jsonl
agent-memory-bridge audit-logs path/to/session.jsonl --stage
agent-memory-bridge audit-logs path/to/session.jsonl --format json
```

## Architectural Invariants

1. **Zero Transcript Persistence**:
   AMB is not a transcript archive. Raw conversational text, user messages, assistant thinking, and tool payloads are never written to durable SQLite tables. Only structured candidate summaries (`trigger`, `fix_or_decision`, `evidence_ref`) are extracted.

2. **Shadow Staging Only (`candidate_status=needs_review`)**:
   Extracted candidates are staged exclusively into the hidden review queue established in #95. They are marked with `is_learning_candidate=1` and `candidate_status="needs_review"`.

3. **Invisibility to Normal Recall**:
   Staged candidates are completely invisible to standard `recall` and `browse` operations. They cannot auto-promote or influence agent retrieval.

4. **Human Confirmation Mandatory**:
   Candidates remain in the review queue until an operator reviews them via `review-queue` or `review-workflow` and explicitly confirms promotion into a durable memory or revision.

5. **Off by Default & Bounded**:
   Log extraction runs only when an operator explicitly executes the command on a designated log file. Candidates are bounded by a configurable limit (default: 10).
