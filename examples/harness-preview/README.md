# Harness Preview Examples

This folder shows the shape of future harness outputs without adding a new AMB
runtime, watcher, scheduler, or MCP tool.

The examples are static, sanitized packets:

- [startup-packet.json](startup-packet.json) shows startup context compiled from
  ordinary memory, gotcha, and signal records.
- [task-packet.json](task-packet.json) shows task-time context with compiled
  truth, evidence, and suppressed stale records.

These files are examples only. They are not source of truth and they are not a
new public API.

