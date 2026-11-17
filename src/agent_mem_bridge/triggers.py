from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Literal, Mapping

from .connectors import SUPPORTED_CONNECTOR_CLIENTS, ConnectorClient, ConnectorScope, normalize_connector_client
from .paths import resolve_bridge_home, resolve_repository_snapshot_root
from .project_resolution import namespace_for_host_adapter, resolve_project_context
from .service_lock import ServiceFileLock, ServiceLockConflict

TriggerMoment = Literal[
    "session_start",
    "prompt_intent",
    "first_write",
    "repeated_failure",
    "subagent_handoff",
    "session_stop",
]
TriggerSupportLevel = Literal[
    "native_hook",
    "deferred_hook",
    "trust_gated_hook",
    "instruction_rule",
]

TRIGGER_MOMENTS: tuple[TriggerMoment, ...] = (
    "session_start",
    "prompt_intent",
    "first_write",
    "repeated_failure",
    "subagent_handoff",
    "session_stop",
)
SUPPORTED_TRIGGER_CLIENTS: tuple[ConnectorClient, ...] = SUPPORTED_CONNECTOR_CLIENTS
TRIGGER_MATRIX_SCHEMA = "amb.trigger-matrix.v1"
TRIGGER_INSTALL_SCHEMA = "amb.trigger-install.v1"
TRIGGER_TEST_SCHEMA = "amb.trigger-test.v1"

MANAGED_BLOCK_START = "<!-- AMB-TRIGGERS:START -->"
MANAGED_BLOCK_END = "<!-- AMB-TRIGGERS:END -->"

TRIGGER_CAPABILITY_MATRIX: dict[ConnectorClient, dict[TriggerMoment, dict[str, str]]] = {
    "claude-code": {
        "session_start": {
            "support": "native_hook",
            "host_event": "SessionStart",
            "output_field": "hookSpecificOutput.additionalContext",
        },
        "prompt_intent": {
            "support": "native_hook",
            "host_event": "UserPromptSubmit",
            "output_field": "hookSpecificOutput.additionalContext",
        },
        "first_write": {
            "support": "native_hook",
            "host_event": "PreToolUse (Edit|Write|MultiEdit|NotebookEdit|Bash)",
            "output_field": "hookSpecificOutput.additionalContext",
        },
        "repeated_failure": {
            "support": "native_hook",
            "host_event": "PostToolUseFailure (2x executed-error counter)",
            "output_field": "hookSpecificOutput.additionalContext",
        },
        "subagent_handoff": {
            "support": "native_hook",
            "host_event": "PreToolUse (Task|Agent)",
            "output_field": "hookSpecificOutput.additionalContext",
        },
        "session_stop": {
            "support": "native_hook",
            "host_event": "Stop (stop_hook_active guard)",
            "output_field": "decision=block, reason",
        },
    },
    "cursor": {
        "session_start": {
            "support": "native_hook",
            "host_event": "sessionStart",
            "output_field": "additional_context",
        },
        "prompt_intent": {
            "support": "deferred_hook",
            "host_event": "beforeSubmitPrompt -> next postToolUse",
            "output_field": "additional_context (on next postToolUse)",
        },
        "first_write": {
            "support": "native_hook",
            "host_event": "postToolUse (after first real write)",
            "output_field": "additional_context",
        },
        "repeated_failure": {
            "support": "native_hook",
            "host_event": "postToolUseFailure (2x counter)",
            "output_field": "additional_context",
        },
        "subagent_handoff": {
            "support": "instruction_rule",
            "host_event": "sessionStart rules (subagentStart is allow/deny only)",
            "output_field": "additional_context (at sessionStart)",
        },
        "session_stop": {
            "support": "native_hook",
            "host_event": "stop (loop_limit=1, loop_count==0)",
            "output_field": "followup_message",
        },
    },
    "codex": {
        "session_start": {
            "support": "trust_gated_hook",
            "host_event": "SessionStart (requires /hooks trust and features.hooks!=false)",
            "output_field": "hookSpecificOutput.additionalContext",
        },
        "prompt_intent": {
            "support": "trust_gated_hook",
            "host_event": "UserPromptSubmit (requires /hooks trust)",
            "output_field": "hookSpecificOutput.additionalContext",
        },
        "first_write": {
            "support": "instruction_rule",
            "host_event": "AGENTS.md managed trigger block",
            "output_field": "instruction_block",
        },
        "repeated_failure": {
            "support": "instruction_rule",
            "host_event": "AGENTS.md managed trigger block",
            "output_field": "instruction_block",
        },
        "subagent_handoff": {
            "support": "instruction_rule",
            "host_event": "AGENTS.md managed trigger block",
            "output_field": "instruction_block",
        },
        "session_stop": {
            "support": "trust_gated_hook",
            "host_event": "Stop (stop_hook_active guard, requires /hooks trust)",
            "output_field": "decision=block, reason",
        },
    },
    "vscode": {
        "session_start": {
            "support": "native_hook",
            "host_event": "sessionStart (.github/hooks/amb-triggers.json)",
            "output_field": "additionalContext",
        },
        "prompt_intent": {
            "support": "deferred_hook",
            "host_event": "userPromptSubmitted (stdout ignored) -> next preToolUse/postToolUse",
            "output_field": "additionalContext (on next tool event)",
        },
        "first_write": {
            "support": "native_hook",
            "host_event": "preToolUse (always exits 0)",
            "output_field": "additionalContext",
        },
        "repeated_failure": {
            "support": "native_hook",
            "host_event": "postToolUse (2x failure counter)",
            "output_field": "additionalContext",
        },
        "subagent_handoff": {
            "support": "native_hook",
            "host_event": "subagentStart",
            "output_field": "additionalContext",
        },
        "session_stop": {
            "support": "native_hook",
            "host_event": "agentStop",
            "output_field": "additionalContext",
        },
    },
    "cline": {
        "session_start": {
            "support": "instruction_rule",
            "host_event": ".clinerules/amb-triggers.md & .cline/rules/amb-triggers.md",
            "output_field": "instruction_block",
        },
        "prompt_intent": {
            "support": "instruction_rule",
            "host_event": ".clinerules/amb-triggers.md & .cline/rules/amb-triggers.md",
            "output_field": "instruction_block",
        },
        "first_write": {
            "support": "instruction_rule",
            "host_event": ".clinerules/amb-triggers.md & .cline/rules/amb-triggers.md",
            "output_field": "instruction_block",
        },
        "repeated_failure": {
            "support": "instruction_rule",
            "host_event": ".clinerules/amb-triggers.md & .cline/rules/amb-triggers.md",
            "output_field": "instruction_block",
        },
        "subagent_handoff": {
            "support": "instruction_rule",
            "host_event": ".clinerules/amb-triggers.md & .cline/rules/amb-triggers.md",
            "output_field": "instruction_block",
        },
        "session_stop": {
            "support": "instruction_rule",
            "host_event": ".clinerules/amb-triggers.md & .cline/rules/amb-triggers.md",
            "output_field": "instruction_block",
        },
    },
}

_INTENT_RE = re.compile(
    r"为什么|为啥|之前|上次|以前|历史|规范|约定|决策|报错|跑不通|失败|崩|回归|继续|接着|"
    r"\bwhy\b|\bprevious(?:ly)?\b|\blast time\b|\bgotcha|\bconvention|\bdecision|"
    r"\bresume\b|\bpick ?up\b|\bregression\b|\bcrash|\berror\b|\bfail",
    re.IGNORECASE,
)
_SHELL_WRITE_RE = re.compile(r"\bsed\s+-i|\btee\b|(?<![0-9&<])>>?\s*(?!/dev/null|&)[^\s|;&]|\b(?:cp|mv|touch)\s")
_WRITE_TOOLS = frozenset(
    {
        "Edit",
        "Write",
        "MultiEdit",
        "NotebookEdit",
        "StrReplace",
        "Delete",
        "EditNotebook",
        "editFiles",
        "createFile",
    }
)
_SHELL_TOOLS = frozenset({"Bash", "Shell", "Terminal", "runInTerminal"})
_SUBAGENT_TOOLS = frozenset({"Task", "Agent", "runSubagent"})
_AMB_READ_OPS = ("recall", "browse", "stats")
_AMB_WRITE_OPS = ("store", "revise", "annotate")


def build_trigger_matrix_report(client: str | None = None) -> dict[str, Any]:
    if client is not None:
        normalized = normalize_connector_client(client)
        clients: tuple[ConnectorClient, ...] = (normalized,)
    else:
        clients = SUPPORTED_TRIGGER_CLIENTS

    matrix = {c: TRIGGER_CAPABILITY_MATRIX[c] for c in clients}
    cell_count = sum(len(TRIGGER_CAPABILITY_MATRIX[c]) for c in clients)
    support_counts: dict[str, int] = {}
    for c in clients:
        for cell in TRIGGER_CAPABILITY_MATRIX[c].values():
            level = cell["support"]
            support_counts[level] = support_counts.get(level, 0) + 1

    return {
        "schema": TRIGGER_MATRIX_SCHEMA,
        "moments": list(TRIGGER_MOMENTS),
        "clients": list(clients),
        "cell_count": cell_count,
        "support_counts": support_counts,
        "matrix": matrix,
    }


def render_trigger_matrix_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# AMB Per-Client Trigger Capability Matrix",
        "",
        "Support levels: `native_hook`, `deferred_hook`, `trust_gated_hook`, `instruction_rule`.",
        "",
        "| Client | `session_start` | `prompt_intent` | `first_write` | `repeated_failure` | `subagent_handoff` | `session_stop` |",
        "|---|---|---|---|---|---|---|",
    ]
    matrix = report.get("matrix") or {}
    for client in report.get("clients") or []:
        row = matrix.get(client) or {}
        cells = [f"`{(row.get(m) or {}).get('support', 'unknown')}`" for m in TRIGGER_MOMENTS]
        lines.append(f"| `{client}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def resolve_trigger_namespace(cwd: str | Path | None) -> str:
    target = Path(cwd or ".").expanduser()
    try:
        resolved = resolve_project_context(target, snapshot_root=resolve_repository_snapshot_root())
        bound = namespace_for_host_adapter(resolved)
        if bound:
            return bound
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        pass
    return "project:<workspace>"


def is_scratch_path(raw_path: str | None) -> bool:
    if not raw_path or not str(raw_path).strip():
        return False
    text = str(raw_path).strip()
    normalized = text.replace("\\", "/").lower()
    posix_temp_prefixes = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
    if normalized in ("/tmp", "/private/tmp") or normalized.startswith(posix_temp_prefixes):
        return True
    if "/appdata/local/temp/" in normalized or normalized.startswith("%temp%") or normalized.startswith("$env:temp"):
        return True
    try:
        sys_temp = Path(tempfile.gettempdir()).resolve()
        candidate = Path(text).expanduser().resolve()
        if candidate == sys_temp or sys_temp in candidate.parents:
            return True
    except OSError:
        pass
    return False


def is_shell_write(command: str | None) -> bool:
    cmd = str(command or "").strip()
    if not cmd or not _SHELL_WRITE_RE.search(cmd):
        return False
    lowered = cmd.replace("\\", "/").lower()
    if any(token in lowered for token in ("/tmp/", "/var/folders/", "/appdata/local/temp/")):
        return False
    return True


def is_triggers_disabled(paths: list[str | Path]) -> bool:
    if os.environ.get("AMB_TRIGGERS_DISABLE") == "1" or os.environ.get("AMB_NUDGE_DISABLE") == "1":
        return True
    for raw in paths:
        if not raw:
            continue
        try:
            current = Path(raw).expanduser().resolve()
        except OSError:
            continue
        for directory in (current, *current.parents):
            if (directory / ".amb-triggers-disable").is_file() or (directory / ".amb-nudge-disable").is_file():
                return True
    return False


def _state_file_path(bridge_home: Path, client: ConnectorClient, session_id: str) -> Path:
    digest = hashlib.sha256((session_id or "default-session").encode("utf-8")).hexdigest()[:24]
    return bridge_home / "triggers" / "state" / client / f"{digest}.json"


def _default_state() -> dict[str, Any]:
    return {
        "amb_reads": 0,
        "amb_writes": 0,
        "writes": 0,
        "tool_failures": 0,
        "write_reminded": False,
        "failure_reminded": False,
        "consolidate_asked": False,
        "intent_pending": False,
    }


def _update_session_state(
    bridge_home: Path,
    client: ConnectorClient,
    session_id: str,
    mutator: Any,
) -> tuple[dict[str, Any], Any]:
    state_path = _state_file_path(bridge_home, client, session_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(".lock")
    with ServiceFileLock(lock_path):
        state = _default_state()
        if state_path.is_file():
            try:
                loaded = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass
        result = mutator(state)
        fd, temp_name = tempfile.mkstemp(prefix=f".{state_path.name}.", suffix=".tmp", dir=state_path.parent)
        temp_path = Path(temp_name)
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, state_path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    return state, result


def _classify_amb_tool(tool_name: str, server_name: str = "", is_mcp_event: bool = False) -> str | None:
    combined = f"{server_name} {tool_name}".lower()
    is_mcp = (
        is_mcp_event
        or tool_name.startswith(("MCP:", "mcp__"))
        or "memory" in combined
        or "bridge" in combined
        or "amb" in combined
    )
    if not is_mcp:
        return None
    for op in _AMB_READ_OPS:
        if combined.endswith(op) or f"_{op}" in combined or f":{op}" in combined or f" {op}" in combined:
            return "read"
    for op in _AMB_WRITE_OPS:
        if combined.endswith(op) or f"_{op}" in combined or f":{op}" in combined or f" {op}" in combined:
            return "write"
    return None


def _is_real_file_write(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    if tool_name in _WRITE_TOOLS:
        target = (
            tool_input.get("file_path")
            or tool_input.get("path")
            or tool_input.get("target_file")
            or tool_input.get("notebook_path")
            or ""
        )
        return not is_scratch_path(str(target))
    if tool_name in _SHELL_TOOLS:
        return is_shell_write(str(tool_input.get("command") or ""))
    return False


def _startup_rules_text(namespace: str) -> str:
    return (
        "[amb-trigger] Agent Memory Bridge is connected. Use it at these moments, one targeted call each: "
        f"(1) start of a task in this project: call `recall` (limit<=5) on `{namespace}`; "
        "(2) when a prompt asks why, refers to earlier work, or reports a bug: recall decisions and gotchas first; "
        "(3) before the first file change if no recall happened yet; "
        "(4) after two failed attempts at the same fix: stop and recall gotchas; "
        "(5) before handing work to a subagent: include the namespace and recalled record IDs; "
        "(6) at the end of work that changed files: list 0-3 writeback candidates and wait for user confirmation before storing."
    )


def run_trigger_hook(
    client: str,
    payload: Mapping[str, Any],
    *,
    bridge_home: Path | None = None,
) -> dict[str, Any]:
    normalized = normalize_connector_client(client)
    resolved_home = (bridge_home or resolve_bridge_home()).expanduser().resolve()

    roots: list[str | Path] = []
    if isinstance(payload.get("workspace_roots"), list):
        roots.extend(str(item) for item in payload["workspace_roots"] if item)
    cwd = str(payload.get("cwd") or os.getcwd())
    roots.append(cwd)

    event = str(payload.get("hook_event_name") or payload.get("event") or "").strip()
    if is_triggers_disabled(roots):
        return {"continue": True} if normalized == "cursor" and event == "beforeSubmitPrompt" else {}

    session_id = str(payload.get("conversation_id") or payload.get("session_id") or "default-session")
    namespace = resolve_trigger_namespace(cwd)

    try:
        if normalized in {"claude-code", "codex"}:
            return _handle_claude_or_codex(normalized, event, payload, resolved_home, session_id, namespace)
        if normalized == "cursor":
            return _handle_cursor(event, payload, resolved_home, session_id, namespace)
        if normalized == "vscode":
            return _handle_vscode(event, payload, resolved_home, session_id, namespace)
        return {}
    except (ServiceLockConflict, OSError, ValueError):
        # Fail open on any lock contention or I/O error
        return {"continue": True} if normalized == "cursor" and event == "beforeSubmitPrompt" else {}


def _claude_context(event: str, text: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def _parse_tool_input(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
    return {}


def _is_non_actionable_failure(payload: Mapping[str, Any]) -> bool:
    if payload.get("is_interrupt") is True:
        return True
    failure_type = str(payload.get("failure_type") or "").strip().lower()
    if failure_type == "permission_denied":
        return True
    has_error_key = "error" in payload or "error_message" in payload
    error_text = str(payload.get("error") or payload.get("error_message") or "").strip()
    if has_error_key and not error_text:
        return True
    if "permission denied" in error_text.lower():
        return True
    return False


def _handle_claude_or_codex(
    client: ConnectorClient,
    event: str,
    payload: Mapping[str, Any],
    bridge_home: Path,
    session_id: str,
    namespace: str,
) -> dict[str, Any]:
    if event == "SessionStart":
        return _claude_context(event, _startup_rules_text(namespace))

    if event == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "")
        if _INTENT_RE.search(prompt):
            return _claude_context(
                event,
                f"[amb-trigger] This prompt has history/bug/resume intent. Run one targeted `recall` (limit<=5) on `{namespace}` for related decisions or gotchas before answering or editing.",
            )
        return {}

    if event == "PreToolUse":
        tool_name = str(payload.get("tool_name") or "")
        tool_input = _parse_tool_input(payload.get("tool_input"))
        amb_kind = _classify_amb_tool(tool_name)

        def _on_pre_tool(st: dict[str, Any]) -> str | None:
            if amb_kind == "read":
                st["amb_reads"] = int(st.get("amb_reads", 0)) + 1
                st["tool_failures"] = 0
                return None
            if amb_kind == "write":
                st["amb_writes"] = int(st.get("amb_writes", 0)) + 1
                return None
            if tool_name in _SUBAGENT_TOOLS:
                return (
                    f"[amb-trigger] Before dispatching a subagent, pass namespace `{namespace}` "
                    "and any recalled AMB record IDs in the handoff prompt."
                )
            if _is_real_file_write(tool_name, tool_input):
                st["writes"] = int(st.get("writes", 0)) + 1
                if not st.get("write_reminded"):
                    st["write_reminded"] = True
                    if int(st.get("amb_reads", 0)) == 0:
                        return (
                            f"[amb-trigger] First file write this session and no AMB recall yet. "
                            f"Run one targeted `recall` on `{namespace}` for decisions or gotchas about the files you are changing."
                        )
            return None

        _, message = _update_session_state(bridge_home, client, session_id, _on_pre_tool)
        return _claude_context(event, message) if message else {}

    if event == "PostToolUseFailure":
        if _is_non_actionable_failure(payload):
            return {}

        def _on_failure(st: dict[str, Any]) -> str | None:
            st["tool_failures"] = int(st.get("tool_failures", 0)) + 1
            if int(st["tool_failures"]) >= 2 and not st.get("failure_reminded"):
                st["failure_reminded"] = True
                return (
                    f"[amb-trigger] Two failed tool attempts in this session. Stop and run one targeted `recall` "
                    f"on `{namespace}` for known gotchas before a third attempt."
                )
            return None

        _, message = _update_session_state(bridge_home, client, session_id, _on_failure)
        return _claude_context(event, message) if message else {}

    if event == "Stop":
        if payload.get("stop_hook_active"):
            return {}

        def _on_stop(st: dict[str, Any]) -> str | None:
            if st.get("consolidate_asked") or int(st.get("writes", 0)) == 0 or int(st.get("amb_writes", 0)) > 0:
                return None
            st["consolidate_asked"] = True
            writes_count = int(st.get("writes", 0))
            return (
                f"[amb-trigger consolidate] This session made {writes_count} file write(s) and no AMB writeback. "
                "List 0-3 writeback candidates (confirmed decision, reproduced gotcha, or changed fact to `revise`), "
                f"each with claim, reason/trigger, and `{namespace}`. Skip linter-enforceable rules and transcripts. "
                "Do NOT store anything until the user confirms."
            )

        _, reason = _update_session_state(bridge_home, client, session_id, _on_stop)
        return {"decision": "block", "reason": reason} if reason else {}

    return {}


def _handle_cursor(
    event: str,
    payload: Mapping[str, Any],
    bridge_home: Path,
    session_id: str,
    namespace: str,
) -> dict[str, Any]:
    if event == "sessionStart":
        return {"additional_context": _startup_rules_text(namespace)}

    if event == "beforeSubmitPrompt":
        prompt = str(payload.get("prompt") or "")
        if _INTENT_RE.search(prompt):

            def _mark_intent(st: dict[str, Any]) -> None:
                st["intent_pending"] = True

            _update_session_state(bridge_home, "cursor", session_id, _mark_intent)
        return {"continue": True}

    if event in {"postToolUse", "afterMCPExecution"}:
        tool_name = str(payload.get("tool_name") or "")
        tool_input = _parse_tool_input(payload.get("tool_input"))
        server = str(payload.get("server_name") or payload.get("server") or payload.get("url") or "")
        amb_kind = _classify_amb_tool(tool_name, server, is_mcp_event=(event == "afterMCPExecution"))

        def _on_post_tool(st: dict[str, Any]) -> list[str]:
            notes: list[str] = []
            if amb_kind == "read":
                st["amb_reads"] = int(st.get("amb_reads", 0)) + 1
                st["tool_failures"] = 0
                st["intent_pending"] = False
            elif amb_kind == "write":
                st["amb_writes"] = int(st.get("amb_writes", 0)) + 1
            elif event == "postToolUse" and _is_real_file_write(tool_name, tool_input):
                st["writes"] = int(st.get("writes", 0)) + 1
                if not st.get("write_reminded"):
                    st["write_reminded"] = True
                    if int(st.get("amb_reads", 0)) == 0:
                        notes.append(
                            f"[amb-trigger] First file change this session and no AMB recall yet. "
                            f"Before further edits, run one targeted `recall` on `{namespace}` for decisions or gotchas."
                        )
            if event == "postToolUse" and st.get("intent_pending") and amb_kind != "read":
                st["intent_pending"] = False
                notes.append(
                    f"[amb-trigger] The current request has history/bug/resume intent. "
                    f"Run one targeted `recall` (limit<=5) on `{namespace}` for related decisions or gotchas."
                )
            return notes

        _, notes = _update_session_state(bridge_home, "cursor", session_id, _on_post_tool)
        return {"additional_context": "\n".join(notes)} if notes else {}

    if event == "postToolUseFailure":
        if _is_non_actionable_failure(payload):
            return {}

        def _on_cursor_failure(st: dict[str, Any]) -> str | None:
            st["tool_failures"] = int(st.get("tool_failures", 0)) + 1
            if int(st["tool_failures"]) >= 2 and not st.get("failure_reminded"):
                st["failure_reminded"] = True
                return (
                    f"[amb-trigger] Two failed tool attempts in this session. Stop and run one targeted `recall` "
                    f"on `{namespace}` for known gotchas before a third attempt."
                )
            return None

        _, note = _update_session_state(bridge_home, "cursor", session_id, _on_cursor_failure)
        return {"additional_context": note} if note else {}

    if event == "stop":
        if int(payload.get("loop_count") or 0) > 0:
            return {}

        def _on_cursor_stop(st: dict[str, Any]) -> str | None:
            if st.get("consolidate_asked") or int(st.get("writes", 0)) == 0 or int(st.get("amb_writes", 0)) > 0:
                return None
            st["consolidate_asked"] = True
            writes_count = int(st.get("writes", 0))
            return (
                f"[amb-trigger consolidate] This session made {writes_count} file change(s) and no AMB writeback. "
                "List 0-3 writeback candidates (confirmed decision, reproduced gotcha, or changed fact to `revise`), "
                f"each with claim, reason/trigger, and `{namespace}`. Do NOT store anything until the user confirms."
            )

        _, followup = _update_session_state(bridge_home, "cursor", session_id, _on_cursor_stop)
        return {"followup_message": followup} if followup else {}

    return {}


def _handle_vscode(
    event: str,
    payload: Mapping[str, Any],
    bridge_home: Path,
    session_id: str,
    namespace: str,
) -> dict[str, Any]:
    if event == "sessionStart":
        return {"additionalContext": _startup_rules_text(namespace)}

    if event == "userPromptSubmitted":
        prompt = str(payload.get("prompt") or "")
        if _INTENT_RE.search(prompt):

            def _mark_vs_intent(st: dict[str, Any]) -> None:
                st["intent_pending"] = True

            _update_session_state(bridge_home, "vscode", session_id, _mark_vs_intent)
        return {}

    if event == "subagentStart":
        return {
            "additionalContext": (
                f"[amb-trigger] Before dispatching a subagent, pass namespace `{namespace}` "
                "and any recalled AMB record IDs in the handoff prompt."
            )
        }

    if event in {"preToolUse", "postToolUse"}:
        tool_name = str(payload.get("toolName") or payload.get("tool_name") or "")
        tool_input = _parse_tool_input(payload.get("toolArgs") or payload.get("tool_input"))
        amb_kind = _classify_amb_tool(tool_name)
        is_failure = bool(payload.get("isError") or payload.get("error")) and not _is_non_actionable_failure(payload)

        def _on_vs_tool(st: dict[str, Any]) -> list[str]:
            notes: list[str] = []
            if amb_kind == "read":
                st["amb_reads"] = int(st.get("amb_reads", 0)) + 1
                st["tool_failures"] = 0
                st["intent_pending"] = False
            elif amb_kind == "write":
                st["amb_writes"] = int(st.get("amb_writes", 0)) + 1
            elif _is_real_file_write(tool_name, tool_input):
                st["writes"] = int(st.get("writes", 0)) + 1
                if not st.get("write_reminded"):
                    st["write_reminded"] = True
                    if int(st.get("amb_reads", 0)) == 0:
                        notes.append(
                            f"[amb-trigger] First file write this session and no AMB recall yet. "
                            f"Run one targeted `recall` on `{namespace}` for decisions or gotchas."
                        )
            if st.get("intent_pending") and amb_kind != "read":
                st["intent_pending"] = False
                notes.append(
                    f"[amb-trigger] The current request has history/bug/resume intent. "
                    f"Run one targeted `recall` (limit<=5) on `{namespace}` for related decisions or gotchas."
                )
            if event == "postToolUse" and is_failure:
                st["tool_failures"] = int(st.get("tool_failures", 0)) + 1
                if int(st["tool_failures"]) >= 2 and not st.get("failure_reminded"):
                    st["failure_reminded"] = True
                    notes.append(
                        f"[amb-trigger] Two failed tool attempts in this session. Stop and run one targeted `recall` "
                        f"on `{namespace}` for known gotchas before a third attempt."
                    )
            return notes

        _, notes = _update_session_state(bridge_home, "vscode", session_id, _on_vs_tool)
        return {"additionalContext": "\n".join(notes)} if notes else {}

    if event == "agentStop":

        def _on_vs_stop(st: dict[str, Any]) -> str | None:
            if st.get("consolidate_asked") or int(st.get("writes", 0)) == 0 or int(st.get("amb_writes", 0)) > 0:
                return None
            st["consolidate_asked"] = True
            writes_count = int(st.get("writes", 0))
            return (
                f"[amb-trigger consolidate] This session made {writes_count} file change(s) and no AMB writeback. "
                "List 0-3 writeback candidates and wait for user confirmation before storing."
            )

        _, note = _update_session_state(bridge_home, "vscode", session_id, _on_vs_stop)
        return {"additionalContext": note} if note else {}

    return {}


def render_managed_instruction_block(namespace: str = "project:<workspace>") -> str:
    return "\n".join(
        [
            MANAGED_BLOCK_START,
            "## Agent Memory Bridge (AMB) Trigger Rules",
            "",
            "Consult Agent Memory Bridge at these six workflow moments (one targeted call each, `limit<=5`):",
            f"1. **Start of a task in this project**: Resolve the namespace with `project resolve .` (or `{namespace}`) and call `recall`.",
            "2. **Why / earlier work / bug or regression prompt**: Call `recall` for related decisions and gotchas before answering or editing.",
            "3. **First file change of the session (when no recall happened yet)**: Call `recall` on the files or module you are changing.",
            "4. **Two failed attempts at the same fix**: Stop and call `recall` for known gotchas before a third attempt.",
            "5. **Before handing work to a subagent**: Pass the resolved `namespace` and recalled record IDs in the handoff.",
            "6. **End of work that changed files**: List 0-3 writeback candidates (decision, reproduced gotcha, or `revise`) and wait for user confirmation before calling `store` or `revise`.",
            MANAGED_BLOCK_END,
        ]
    )


def _upsert_managed_block(target: Path, block: str) -> str:
    if not target.exists():
        return block + "\n"
    existing = target.read_text(encoding="utf-8")
    if MANAGED_BLOCK_START in existing and MANAGED_BLOCK_END in existing:
        start_idx = existing.index(MANAGED_BLOCK_START)
        end_idx = existing.index(MANAGED_BLOCK_END) + len(MANAGED_BLOCK_END)
        return existing[:start_idx] + block + existing[end_idx:]
    if existing.strip():
        return existing.rstrip() + "\n\n" + block + "\n"
    return block + "\n"


def _hook_command_string(python_path: str | Path | None, client: ConnectorClient) -> str:
    raw = Path(python_path or sys.executable).expanduser()
    abs_path = Path(os.path.abspath(raw))
    executable = str(abs_path.parent.resolve() / abs_path.name)
    if "\n" in executable or "\r" in executable or '"' in executable:
        raise ValueError("Python executable path must not contain quotes or newline characters.")
    quoted_exec = f'"{executable}"' if " " in executable else executable
    return f"{quoted_exec} -I -m agent_mem_bridge trigger-hook --client {client}"


def install_client_triggers(
    client: str,
    *,
    scope: ConnectorScope = "project",
    cwd: Path | None = None,
    home: Path | None = None,
    python_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    normalized = normalize_connector_client(client)
    resolved_cwd = (cwd or Path.cwd()).expanduser().resolve()
    resolved_home = (home or Path.home()).expanduser().resolve()
    namespace = resolve_trigger_namespace(resolved_cwd)
    cmd_str = _hook_command_string(python_path, normalized)
    files_written: list[str] = []
    warnings: list[str] = []

    if normalized == "claude-code":
        settings_path = (
            resolved_cwd / ".claude" / "settings.json"
            if scope == "project"
            else resolved_home / ".claude" / "settings.json"
        )
        merged = _merge_claude_settings_hooks(settings_path, cmd_str)
        if not dry_run:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(merged, encoding="utf-8")
        files_written.append(str(settings_path))
        delivery_mode = "native_hooks"

    elif normalized == "cursor":
        hooks_path = (
            resolved_cwd / ".cursor" / "hooks.json" if scope == "project" else resolved_home / ".cursor" / "hooks.json"
        )
        merged = _merge_cursor_hooks_json(hooks_path, cmd_str)
        if not dry_run:
            hooks_path.parent.mkdir(parents=True, exist_ok=True)
            hooks_path.write_text(merged, encoding="utf-8")
        files_written.append(str(hooks_path))
        delivery_mode = "native_and_deferred_hooks"

    elif normalized == "codex":
        hooks_path = (
            resolved_cwd / ".codex" / "hooks.json" if scope == "project" else resolved_home / ".codex" / "hooks.json"
        )
        config_toml = hooks_path.parent / "config.toml"
        if config_toml.is_file():
            try:
                parsed_toml = tomllib.loads(config_toml.read_text(encoding="utf-8"))
                if (parsed_toml.get("features") or {}).get("hooks") is False:
                    warnings.append(
                        "Codex `config.toml` has `[features].hooks = false`; enable hooks for native triggers."
                    )
            except tomllib.TOMLDecodeError:
                pass
        warnings.append(
            "Codex hooks require interactive `/hooks` trust approval before Codex executes new or updated hooks."
        )
        rendered_hooks = _merge_codex_hooks_json(hooks_path, cmd_str)
        agents_md = resolved_cwd / "AGENTS.md" if scope == "project" else resolved_home / ".codex" / "AGENTS.md"
        rendered_agents = _upsert_managed_block(agents_md, render_managed_instruction_block(namespace))
        if not dry_run:
            hooks_path.parent.mkdir(parents=True, exist_ok=True)
            hooks_path.write_text(rendered_hooks, encoding="utf-8")
            agents_md.parent.mkdir(parents=True, exist_ok=True)
            agents_md.write_text(rendered_agents, encoding="utf-8")
        files_written.extend([str(hooks_path), str(agents_md)])
        delivery_mode = "trust_gated_hooks_plus_instructions"

    elif normalized == "vscode":
        copilot_home = os.environ.get("COPILOT_HOME", "").strip()
        user_copilot_root = Path(copilot_home).expanduser().resolve() if copilot_home else resolved_home / ".copilot"
        hooks_path = (
            resolved_cwd / ".github" / "hooks" / "amb-triggers.json"
            if scope == "project"
            else user_copilot_root / "hooks" / "amb-triggers.json"
        )
        rendered_hooks = _merge_vscode_hooks_json(hooks_path, cmd_str)
        instructions_path = resolved_cwd / ".github" / "copilot-instructions.md"
        rendered_instructions = _upsert_managed_block(
            instructions_path,
            render_managed_instruction_block(namespace),
        )
        if not dry_run:
            hooks_path.parent.mkdir(parents=True, exist_ok=True)
            hooks_path.write_text(rendered_hooks, encoding="utf-8")
            if scope == "project":
                instructions_path.parent.mkdir(parents=True, exist_ok=True)
                instructions_path.write_text(rendered_instructions, encoding="utf-8")
        files_written.append(str(hooks_path))
        if scope == "project":
            files_written.append(str(instructions_path))
        delivery_mode = "native_and_deferred_hooks_plus_instructions"

    else:
        # normalized == "cline"
        block = render_managed_instruction_block(namespace)
        targets = (
            [
                resolved_cwd / ".clinerules" / "amb-triggers.md",
                resolved_cwd / ".cline" / "rules" / "amb-triggers.md",
            ]
            if scope == "project"
            else [
                resolved_home / "Documents" / "Cline" / "Rules" / "amb-triggers.md",
                resolved_home / ".cline" / "rules" / "amb-triggers.md",
                resolved_home / "Cline" / "Rules" / "amb-triggers.md",
            ]
        )
        for target in targets:
            rendered = _upsert_managed_block(target, block)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(rendered, encoding="utf-8")
            files_written.append(str(target))
        warnings.append(
            "Cline uses instruction rules (`.clinerules/` and `.cline/rules/`); no native command hooks are claimed."
        )
        delivery_mode = "instruction_only"

    return {
        "schema": TRIGGER_INSTALL_SCHEMA,
        "ok": True,
        "dry_run": dry_run,
        "client": normalized,
        "scope": scope,
        "delivery_mode": delivery_mode,
        "files": files_written,
        "capabilities": TRIGGER_CAPABILITY_MATRIX[normalized],
        "warnings": warnings,
    }


def _merge_claude_settings_hooks(settings_path: Path, cmd_str: str) -> str:
    if settings_path.is_file():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks

    def _upsert_event(event_name: str, matcher: str | None = None) -> None:
        entries = hooks.setdefault(event_name, [])
        if not isinstance(entries, list):
            entries = []
            hooks[event_name] = entries
        filtered = [
            item
            for item in entries
            if not (
                isinstance(item, dict)
                and any(
                    isinstance(h, dict) and "trigger-hook --client claude-code" in str(h.get("command") or "")
                    for h in (item.get("hooks") or [])
                )
                and item.get("matcher") == matcher
            )
        ]
        new_entry: dict[str, Any] = {"hooks": [{"type": "command", "command": cmd_str}]}
        if matcher is not None:
            new_entry["matcher"] = matcher
        filtered.append(new_entry)
        hooks[event_name] = filtered

    _upsert_event("SessionStart")
    _upsert_event("UserPromptSubmit")
    _upsert_event("PreToolUse", "Edit|Write|NotebookEdit|MultiEdit|Bash")
    _upsert_event("PreToolUse", "Task|Agent")
    _upsert_event("PostToolUseFailure")
    _upsert_event("Stop")
    return json.dumps(data, indent=2) + "\n"


def _merge_cursor_hooks_json(hooks_path: Path, cmd_str: str) -> str:
    if hooks_path.is_file():
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
    else:
        data = {"version": 1, "hooks": {}}
    if not isinstance(data, dict):
        data = {"version": 1, "hooks": {}}
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks

    for event_name in (
        "sessionStart",
        "beforeSubmitPrompt",
        "postToolUse",
        "postToolUseFailure",
        "afterMCPExecution",
        "stop",
    ):
        entries = hooks.setdefault(event_name, [])
        if not isinstance(entries, list):
            entries = []
        filtered = [
            item
            for item in entries
            if not (isinstance(item, dict) and "trigger-hook --client cursor" in str(item.get("command") or ""))
        ]
        entry: dict[str, Any] = {"command": cmd_str}
        if event_name == "stop":
            entry["loop_limit"] = 1
        filtered.append(entry)
        hooks[event_name] = filtered

    return json.dumps(data, indent=2) + "\n"


def _merge_codex_hooks_json(hooks_path: Path, cmd_str: str) -> str:
    if hooks_path.is_file():
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks

    for event_name in ("SessionStart", "UserPromptSubmit", "Stop"):
        entries = hooks.setdefault(event_name, [])
        if not isinstance(entries, list):
            entries = []
        filtered = [
            item
            for item in entries
            if not (
                isinstance(item, dict)
                and any(
                    isinstance(h, dict) and "trigger-hook --client codex" in str(h.get("command") or "")
                    for h in (item.get("hooks") or [])
                )
            )
        ]
        filtered.append({"hooks": [{"type": "command", "command": cmd_str}]})
        hooks[event_name] = filtered

    return json.dumps(data, indent=2) + "\n"


def _merge_vscode_hooks_json(hooks_path: Path, cmd_str: str) -> str:
    if hooks_path.is_file():
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks

    for event_name in (
        "sessionStart",
        "userPromptSubmitted",
        "preToolUse",
        "postToolUse",
        "subagentStart",
        "agentStop",
    ):
        entries = hooks.setdefault(event_name, [])
        if not isinstance(entries, list):
            entries = []
        filtered = [
            item
            for item in entries
            if not (isinstance(item, dict) and "trigger-hook --client vscode" in str(item.get("command") or ""))
        ]
        filtered.append({"type": "command", "command": cmd_str})
        hooks[event_name] = filtered

    return json.dumps(data, indent=2) + "\n"


def _installed_hook_config_path(
    client: ConnectorClient,
    *,
    scope: ConnectorScope,
    cwd: Path,
    home: Path,
) -> Path | None:
    if client == "claude-code":
        return cwd / ".claude" / "settings.json" if scope == "project" else home / ".claude" / "settings.json"
    if client == "cursor":
        return cwd / ".cursor" / "hooks.json" if scope == "project" else home / ".cursor" / "hooks.json"
    if client == "codex":
        return cwd / ".codex" / "hooks.json" if scope == "project" else home / ".codex" / "hooks.json"
    if client == "vscode":
        if scope == "project":
            return cwd / ".github" / "hooks" / "amb-triggers.json"
        copilot_home = os.environ.get("COPILOT_HOME", "").strip()
        user_root = Path(copilot_home).expanduser() if copilot_home else home / ".copilot"
        return user_root / "hooks" / "amb-triggers.json"
    return None


def _collect_trigger_hook_commands(node: Any, marker: str, found: list[str]) -> None:
    if isinstance(node, dict):
        command = node.get("command")
        if isinstance(command, str) and marker in command:
            found.append(command)
        for value in node.values():
            _collect_trigger_hook_commands(value, marker, found)
    elif isinstance(node, list):
        for item in node:
            _collect_trigger_hook_commands(item, marker, found)


def _installed_trigger_commands(
    client: ConnectorClient,
    *,
    scope: ConnectorScope,
    cwd: Path,
    home: Path,
) -> list[str]:
    config_path = _installed_hook_config_path(client, scope=scope, cwd=cwd, home=home)
    if config_path is None or not config_path.is_file():
        return []
    try:
        parsed = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    marker = f"trigger-hook --client {client}"
    found: list[str] = []
    _collect_trigger_hook_commands(parsed, marker, found)
    distinct: list[str] = []
    for command in found:
        if command not in distinct:
            distinct.append(command)
    return distinct


def _session_start_hook_payload(client: ConnectorClient) -> dict[str, str]:
    if client in {"claude-code", "codex"}:
        return {"hook_event_name": "SessionStart", "session_id": f"verify-{client}"}
    return {"hook_event_name": "sessionStart", "session_id": f"verify-{client}"}


def _hook_output_has_session_context(client: ConnectorClient, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if client == "cursor":
        return "additional_context" in payload
    if client == "vscode":
        return "additionalContext" in payload
    return "additionalContext" in (payload.get("hookSpecificOutput") or {})


def _split_hook_command(command: str) -> list[str]:
    tokens = shlex.split(command, posix=(os.name != "nt"))
    if os.name == "nt":
        cleaned: list[str] = []
        for token in tokens:
            if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
                cleaned.append(token[1:-1])
            else:
                cleaned.append(token)
        return cleaned
    return tokens


def _verify_installed_hook_command(
    client: ConnectorClient,
    *,
    scope: ConnectorScope,
    cwd: Path,
    home: Path,
    temp_home: Path,
) -> dict[str, Any]:
    installed_cmds = _installed_trigger_commands(client, scope=scope, cwd=cwd, home=home)
    commands = installed_cmds or [_hook_command_string(None, client)]
    payload = json.dumps(_session_start_hook_payload(client))
    env = {**os.environ, "AGENT_MEMORY_BRIDGE_HOME": str(temp_home)}
    failures: list[str] = []
    checked_command = commands[-1]
    for command in commands:
        try:
            completed = subprocess.run(
                _split_hook_command(command),
                input=payload,
                text=True,
                capture_output=True,
                env=env,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            failures.append(f"{command}: {exc}")
            checked_command = command
            continue
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            failures.append(f"{command}: exit {completed.returncode}" + (f": {detail}" if detail else ""))
            checked_command = command
            continue
        try:
            parsed = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            failures.append(f"{command}: invalid JSON output: {exc}")
            checked_command = command
            continue
        if not _hook_output_has_session_context(client, parsed):
            failures.append(f"{command}: missing SessionStart context")
            checked_command = command
            continue
        checked_command = command
    item: dict[str, Any] = {
        "moment": "installed_command",
        "command": checked_command,
        "installed": bool(installed_cmds),
        "ok": not failures,
    }
    if failures:
        item["error"] = "; ".join(failures)
    return item


def verify_client_triggers(
    client: str,
    *,
    bridge_home: Path | None = None,
    scope: ConnectorScope = "project",
    cwd: Path | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    normalized = normalize_connector_client(client)
    resolved_cwd = (cwd or Path.cwd()).expanduser()
    resolved_home = (home or Path.home()).expanduser()
    with tempfile.TemporaryDirectory(prefix=f"amb-trigger-test-{normalized}-") as temp_dir:
        temp_home = (bridge_home or Path(temp_dir) / "bridge-home").resolve()
        temp_home.mkdir(parents=True, exist_ok=True)
        sid = f"test-session-{normalized}"
        checks: list[dict[str, Any]] = []

        if normalized == "cline":
            block = render_managed_instruction_block("project:demo")
            ok = MANAGED_BLOCK_START in block and "six workflow moments" in block
            checks.append({"moment": "instruction_block", "support": "instruction_rule", "ok": ok})
            return {
                "schema": TRIGGER_TEST_SCHEMA,
                "ok": ok,
                "client": normalized,
                "checks": checks,
            }

        if normalized in {"claude-code", "codex"}:
            start_out = run_trigger_hook(
                normalized, {"hook_event_name": "SessionStart", "session_id": sid}, bridge_home=temp_home
            )
            checks.append(
                {
                    "moment": "session_start",
                    "ok": "additionalContext" in (start_out.get("hookSpecificOutput") or {}),
                }
            )
            prompt_out = run_trigger_hook(
                normalized,
                {"hook_event_name": "UserPromptSubmit", "session_id": sid, "prompt": "Why did we choose this schema?"},
                bridge_home=temp_home,
            )
            checks.append(
                {
                    "moment": "prompt_intent",
                    "ok": "additionalContext" in (prompt_out.get("hookSpecificOutput") or {}),
                }
            )
            write_out = run_trigger_hook(
                normalized,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": sid,
                    "tool_name": "Write",
                    "tool_input": {"file_path": "/workspace/app.py"},
                },
                bridge_home=temp_home,
            )
            write_repeat = run_trigger_hook(
                normalized,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": sid,
                    "tool_name": "Write",
                    "tool_input": {"file_path": "/workspace/app2.py"},
                },
                bridge_home=temp_home,
            )
            checks.append(
                {
                    "moment": "first_write",
                    "ok": "additionalContext" in (write_out.get("hookSpecificOutput") or {}) and write_repeat == {},
                }
            )
            fail_1 = run_trigger_hook(
                normalized,
                {"hook_event_name": "PostToolUseFailure", "session_id": sid, "is_interrupt": False},
                bridge_home=temp_home,
            )
            fail_2 = run_trigger_hook(
                normalized,
                {"hook_event_name": "PostToolUseFailure", "session_id": sid, "is_interrupt": False},
                bridge_home=temp_home,
            )
            checks.append(
                {
                    "moment": "repeated_failure",
                    "ok": fail_1 == {} and "additionalContext" in (fail_2.get("hookSpecificOutput") or {}),
                }
            )
            handoff_out = run_trigger_hook(
                normalized,
                {"hook_event_name": "PreToolUse", "session_id": sid, "tool_name": "Task", "tool_input": {}},
                bridge_home=temp_home,
            )
            checks.append(
                {
                    "moment": "subagent_handoff",
                    "ok": "additionalContext" in (handoff_out.get("hookSpecificOutput") or {}),
                }
            )
            stop_1 = run_trigger_hook(
                normalized,
                {"hook_event_name": "Stop", "session_id": sid, "stop_hook_active": False},
                bridge_home=temp_home,
            )
            stop_guard = run_trigger_hook(
                normalized,
                {"hook_event_name": "Stop", "session_id": sid, "stop_hook_active": True},
                bridge_home=temp_home,
            )
            checks.append(
                {
                    "moment": "session_stop",
                    "ok": stop_1.get("decision") == "block" and stop_guard == {},
                }
            )

        elif normalized == "cursor":
            start_out = run_trigger_hook(
                "cursor", {"hook_event_name": "sessionStart", "conversation_id": sid}, bridge_home=temp_home
            )
            checks.append({"moment": "session_start", "ok": "additional_context" in start_out})

            submit_out = run_trigger_hook(
                "cursor",
                {
                    "hook_event_name": "beforeSubmitPrompt",
                    "conversation_id": sid,
                    "prompt": "Why did tests fail last time?",
                },
                bridge_home=temp_home,
            )
            post_out = run_trigger_hook(
                "cursor",
                {
                    "hook_event_name": "postToolUse",
                    "conversation_id": sid,
                    "tool_name": "Write",
                    "tool_input": {"path": "/workspace/main.py"},
                },
                bridge_home=temp_home,
            )
            checks.append(
                {
                    "moment": "prompt_intent_and_first_write",
                    "ok": submit_out == {"continue": True} and "additional_context" in post_out,
                }
            )
            fail_1 = run_trigger_hook(
                "cursor",
                {"hook_event_name": "postToolUseFailure", "conversation_id": sid},
                bridge_home=temp_home,
            )
            fail_2 = run_trigger_hook(
                "cursor",
                {"hook_event_name": "postToolUseFailure", "conversation_id": sid},
                bridge_home=temp_home,
            )
            checks.append({"moment": "repeated_failure", "ok": fail_1 == {} and "additional_context" in fail_2})

            stop_1 = run_trigger_hook(
                "cursor",
                {"hook_event_name": "stop", "conversation_id": sid, "loop_count": 0},
                bridge_home=temp_home,
            )
            stop_guard = run_trigger_hook(
                "cursor",
                {"hook_event_name": "stop", "conversation_id": sid, "loop_count": 1},
                bridge_home=temp_home,
            )
            checks.append({"moment": "session_stop", "ok": "followup_message" in stop_1 and stop_guard == {}})

        elif normalized == "vscode":
            start_out = run_trigger_hook(
                "vscode", {"hook_event_name": "sessionStart", "session_id": sid}, bridge_home=temp_home
            )
            checks.append({"moment": "session_start", "ok": "additionalContext" in start_out})

            prompt_out = run_trigger_hook(
                "vscode",
                {
                    "hook_event_name": "userPromptSubmitted",
                    "session_id": sid,
                    "prompt": "Why did this regression happen?",
                },
                bridge_home=temp_home,
            )
            pre_write = run_trigger_hook(
                "vscode",
                {
                    "hook_event_name": "preToolUse",
                    "session_id": sid,
                    "toolName": "editFiles",
                    "toolArgs": {"path": "/workspace/src/index.ts"},
                },
                bridge_home=temp_home,
            )
            checks.append(
                {
                    "moment": "prompt_intent_deferred_and_first_write",
                    "ok": prompt_out == {} and "additionalContext" in pre_write,
                }
            )
            fail_1 = run_trigger_hook(
                "vscode",
                {"hook_event_name": "postToolUse", "session_id": sid, "toolName": "runInTerminal", "isError": True},
                bridge_home=temp_home,
            )
            fail_2 = run_trigger_hook(
                "vscode",
                {"hook_event_name": "postToolUse", "session_id": sid, "toolName": "runInTerminal", "isError": True},
                bridge_home=temp_home,
            )
            checks.append({"moment": "repeated_failure", "ok": fail_1 == {} and "additionalContext" in fail_2})

            sub_out = run_trigger_hook(
                "vscode",
                {"hook_event_name": "subagentStart", "session_id": sid},
                bridge_home=temp_home,
            )
            checks.append({"moment": "subagent_handoff", "ok": "additionalContext" in sub_out})

            stop_out = run_trigger_hook(
                "vscode",
                {"hook_event_name": "agentStop", "session_id": sid},
                bridge_home=temp_home,
            )
            checks.append({"moment": "session_stop", "ok": "additionalContext" in stop_out})

        checks.append(
            _verify_installed_hook_command(
                normalized,
                scope=scope,
                cwd=resolved_cwd,
                home=resolved_home,
                temp_home=temp_home,
            )
        )

        return {
            "schema": TRIGGER_TEST_SCHEMA,
            "ok": all(bool(item["ok"]) for item in checks),
            "client": normalized,
            "checks": checks,
        }
