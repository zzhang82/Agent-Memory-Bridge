# Model Routing

This project should choose models the same way it chooses architecture: by task shape, not by habit.

## Core Rule

Use the strongest reasoning model only when ambiguity, synthesis, or architectural risk justifies it.

Use coding-optimized or smaller models only when the task is bounded enough that speed matters more than broad reasoning.

## Default Routing

### Product definition, architecture, validation

- Model: `gpt-5.4`
- Reasoning: `high` or `xhigh`
- Use when:
  - scope is unclear
  - trade-offs are non-obvious
  - decisions affect the whole system
  - we need a skeptical review after implementation

Why:
- OpenAI's code generation guidance says to start with `gpt-5.4` for most coding tasks and broader workflows, especially when the work includes reasoning about requirements and mixed tasks.

Source:
- [Code generation guide](https://developers.openai.com/api/docs/guides/code-generation)

### Main implementation work

- Model: `gpt-5.4`
- Reasoning: `medium` or `high`
- Use when:
  - implementing a feature across multiple files
  - writing tests plus code
  - refactoring storage, APIs, or behavior

Why:
- This project mixes design, Python code, and MCP integration. The general-purpose model is the safest default.

### Bounded worker tasks

- Model: `gpt-5.3-codex`
- Reasoning: `medium` or `high`
- Use when:
  - one file or one module has clear ownership
  - the contract is already decided
  - the worker is not responsible for product direction

Why:
- Cole's team/workflow notes favor delegation only when the task is partitionable and the synchronization cost is low.

### Small utility or read-only checks

- Model: `gpt-5.4-mini` or equivalent small coding worker
- Reasoning: `low` or `medium`
- Use when:
  - formatting or rote edits
  - narrow file inspection
  - quick verification that does not drive architecture

## Reasoning Policy

- `low`: only for trivial edits or lookups
- `medium`: default for implementation once the contract is clear
- `high`: default for important code changes or non-trivial debugging
- `xhigh`: reserve for product shaping, architecture, and final validation

## Project-Specific Guidance

For `agent-memory-bridge`, use this sequence:

1. `gpt-5.4` with `high` or `xhigh` to define the product slice.
2. `gpt-5.4` or `gpt-5.3-codex` with `medium` or `high` to implement bounded milestones.
3. `gpt-5.4` with `high` to validate the milestone against the PRD.

That matches the project goal:

- avoid speculative features
- prove the smallest useful slice
- validate before expanding

## Current Near-Term Plan

- Benchmark and retrieval evaluation: `gpt-5.4`, `high`
- Codex conversation-ingest design: `gpt-5.4`, `high`
- Bounded code patches after the ingest contract is settled: `gpt-5.3-codex`, `medium`

