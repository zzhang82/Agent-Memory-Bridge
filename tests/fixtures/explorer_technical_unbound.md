# AMB Knowledge Explorer

Namespace: `project:fixture`

> The graph is a read-only, rebuildable projection. It is not a source of truth.

Repository binding: `unbound`

## Relationships

- **Python support** — `supports` → **Local first**
  - authority: `governed_durable_memory`
  - source: `{"authority": "governed_durable_memory", "eligibility": "existing_governance", "memory_id": "memory-constraint-1", "target_memory_id": "memory-decision-1"}`
- **project:fixture** — `has_constraint` → **Python support**
  - authority: `governed_durable_memory`
  - source: `{"authority": "governed_durable_memory", "eligibility": "existing_governance", "memory_id": "memory-constraint-1"}`
- **project:fixture** — `has_decision` → **Local first**
  - authority: `governed_durable_memory`
  - source: `{"authority": "governed_durable_memory", "eligibility": "existing_governance", "memory_id": "memory-decision-1"}`
