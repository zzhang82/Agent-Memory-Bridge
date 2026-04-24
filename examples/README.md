# Examples

This folder contains small public demo artifacts for Agent Memory Bridge.

It is intentionally limited to sanitized examples that show the shape of:

- a high-level bridge overview diagram in [diagrams/amb-overview.svg](diagrams/amb-overview.svg)
- a closeout payload
- a durable memory note
- a handoff signal note
- a reusable terminal demo source in [demo](demo/README.md)

The canonical current demo lives in `demo/terminal-demo.*`.
Any `v0.5` demo artifacts are historical only.

Machine-specific reports and local validation output are generated during development,
but those files are ignored and are not part of the public example set.

When you generate fresh healthcheck or watcher reports locally, write them under
`.runtime/` or another ignored path rather than back into `examples/`.
