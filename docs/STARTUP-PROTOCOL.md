# Startup Protocol

This document defines the recommended startup order when using Agent Memory Bridge with a system-level operating profile.

## Goal

Keep the durable operating profile system-level, keep project instructions thin, and avoid re-copying the same core rules into every repository.

## Layering

### System Level

- `agentMemoryBridge` provides the shared memory substrate
- a system-level operator profile provides operating behavior
- global profile namespaces provide durable cross-project operating context

### Project Level

- `AGENTS.md` should only add project-local expectations
- it should not restate the full global operator profile
- it should assume the global operator profile already exists

## Session Start Order

When a session is meant to operate with a system-level operator profile:

1. Recall the global operating namespace
2. Recall the relevant specialization namespace if the task clearly matches one:
   - team memory
   - workflow memory
   - skill memory
   - workspace memory
3. Recall the active `project:<workspace>` namespace whenever there is a current workspace, and treat it as part of the default startup stack rather than an optional extra
4. For issue-like work, also check:
   - current project gotchas
   - global gotchas
   - relevant domain notes
5. Only then:
   - inspect the live codebase
   - browse external sources if needed

When a workspace-backed session explains its startup protocol, it should explicitly name the `project:<workspace>` layer alongside the system-level global namespaces.

## Why This Split

The system-level profile should travel across repositories.

The project-level layer should stay small and answer only:

- what is special about this repository
- what commands or conventions matter here
- what local guardrails override the default operator behavior

## Writeback Rule

At the end of meaningful work:

- write durable global lessons into the active global operating namespace
- write project-specific memory into `project:<workspace>`
- keep records machine-readable and compact

## What Not To Do

- do not duplicate the entire operator core in every repo `AGENTS.md`
- do not treat `AGENTS.md` as the system memory itself
- do not skip live code inspection when current implementation truth matters
