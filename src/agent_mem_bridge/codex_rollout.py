from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROLLOUT_RE = re.compile(r"rollout-(?P<timestamp>.+)-(?P<thread>[0-9a-f-]{36})\.jsonl$", re.IGNORECASE)
CHECKPOINT_MARKERS = (
    "decision",
    "fix",
    "fixed",
    "problem",
    "symptom",
    "claim",
    "trigger",
    "root cause",
    "cause",
    "regression",
    "bug",
    "error",
    "issue",
    "handoff",
    "gotcha",
    "recall",
    "memory",
    "checkpoint",
    "drift",
    "wrong db",
    "validated",
)
CHECKPOINT_LABEL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Fix", ("fix:", "fixed", "solution", "resolved", "use one canonical", "keep ", "assign ")),
    (
        "Problem",
        ("problem:", "wrong db", "bug", "error", "issue", "drift", "regression", "missing", "fails", "failure"),
    ),
    ("Decision", ("decision:", "decision", "prefer", "should", "must", "use high reasoning", "do not", "need to")),
    ("Trigger", ("trigger:", "when ", "if ", "trigger", "during ", "after ")),
    ("Claim", ("claim:", "recall", "memory", "checkpoint", "validated", "works", "loaded")),
)
CHECKPOINT_NOISE_PATTERNS = (
    "if you want",
    "worked for",
    "yes, ",
    "yes.",
    "sure ",
    "check if ",
    "can also test",
    "what it has captured",
    "what it has not fully captured",
    "i verified it",
    "i can ",
    "i'm rerunning",
    "i’m rerunning",
    "i'm pushing",
    "i’m pushing",
)
EXPLICIT_CHECKPOINT_PREFIXES = ("Claim:", "Decision:", "Fix:", "Problem:", "Trigger:", "Symptom:")
EXPLICIT_CLOSE_EVENT_TYPES = frozenset({"session_end", "rollout_closed", "thread_closed"})
ROLLOUT_CURSOR_VERSION = 1
ROLLOUT_CURSOR_ANCHOR_BYTES = 4096


@dataclass(slots=True)
class RolloutSummary:
    thread_id: str
    session_timestamp: str
    cwd: str
    source: str
    forked_from_id: str
    agent_nickname: str
    agent_role: str
    user_messages: list[str]
    assistant_messages: list[str]
    last_updated: str | None
    user_message_count: int = 0
    assistant_message_count: int = 0
    checkpoint_signal: bool = False
    explicit_close_reason: str = ""
    declared_goal: str = ""
    scan_start_offset: int = 0
    scan_bytes_read: int = 0


def parse_rollout_file(path: Path) -> RolloutSummary:
    thread_id = ""
    session_timestamp = ""
    cwd = ""
    source = "codex"
    forked_from_id = ""
    agent_nickname = ""
    agent_role = ""
    last_updated: str | None = None
    user_messages: list[str] = []
    assistant_messages: list[str] = []
    checkpoint_signal = False
    explicit_close_reason = ""
    declared_goal = ""

    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError:
            # Rollout files are append-only and can be observed while Codex is
            # still writing the final line. Treat malformed lines as an
            # incomplete tail instead of crashing the long-running service.
            continue
        last_updated = item.get("timestamp") or last_updated
        item_type = item.get("type")
        payload = item.get("payload", {})

        if item_type == "session_meta":
            candidate_id = str(payload.get("id") or "").strip()
            if candidate_id and not thread_id:
                thread_id = candidate_id
            session_timestamp = payload.get("timestamp", session_timestamp)
            cwd = payload.get("cwd", cwd)
            source = payload.get("originator", source)
            forked_from_id = payload.get("forked_from_id", forked_from_id)
            agent_nickname = payload.get("agent_nickname", agent_nickname)
            agent_role = payload.get("agent_role", agent_role)
            declared_goal = _bounded_declared_goal(payload.get("task") or payload.get("goal") or declared_goal)
            continue

        if item_type == "event_msg" and payload.get("type") == "user_message":
            message = (payload.get("message") or "").strip()
            if message:
                user_messages.append(message)
                checkpoint_signal = checkpoint_signal or _is_high_signal_message(message)
            continue

        if item_type == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant":
            text = extract_message_text(payload)
            if text:
                assistant_messages.append(text)
                checkpoint_signal = checkpoint_signal or _is_high_signal_message(text)

        close_reason = _explicit_close_reason(item_type, payload)
        if close_reason:
            explicit_close_reason = close_reason

    if not thread_id:
        match = ROLLOUT_RE.search(Path(path).name)
        if match:
            thread_id = match.group("thread")

    return RolloutSummary(
        thread_id=thread_id,
        session_timestamp=session_timestamp,
        cwd=cwd,
        source=source,
        forked_from_id=forked_from_id,
        agent_nickname=agent_nickname,
        agent_role=agent_role,
        user_messages=_dedupe_preserve_order(user_messages),
        assistant_messages=_dedupe_preserve_order(assistant_messages),
        last_updated=last_updated,
        user_message_count=len(user_messages),
        assistant_message_count=len(assistant_messages),
        checkpoint_signal=checkpoint_signal,
        explicit_close_reason=explicit_close_reason,
        declared_goal=declared_goal,
    )


def scan_rollout_file_incremental(
    path: Path,
    cursor: Mapping[str, Any] | None = None,
) -> tuple[RolloutSummary, dict[str, Any]]:
    """Read only complete JSONL bytes appended since the last watcher scan.

    The returned cursor retains bounded metadata, counts, file identity, offsets,
    and digests only. Message bodies are used transiently for checkpoint-signal
    detection and are never written into watcher state.
    """

    rollout_path = Path(path)
    stat = rollout_path.stat()
    file_identity = _rollout_file_identity(rollout_path, stat)
    stored = dict(cursor) if isinstance(cursor, Mapping) else {}
    reset = not _cursor_matches_file(rollout_path, stat, file_identity, stored)
    start_offset = 0 if reset else _cursor_nonnegative_int(stored, "byte_offset")
    rolling_digest = "" if reset else _cursor_text(stored, "rolling_digest")
    thread_id = "" if reset else _cursor_text(stored, "thread_id")
    session_timestamp = "" if reset else _cursor_text(stored, "session_timestamp")
    workspace_name = "" if reset else _cursor_text(stored, "workspace_name")
    source = "codex" if reset else (_cursor_text(stored, "source") or "codex")
    forked_from_id = "" if reset else _cursor_text(stored, "forked_from_id")
    agent_nickname = "" if reset else _cursor_text(stored, "agent_nickname")
    agent_role = "" if reset else _cursor_text(stored, "agent_role")
    last_updated = None if reset else (_cursor_text(stored, "last_updated") or None)
    user_count = 0 if reset else _cursor_nonnegative_int(stored, "user_message_count")
    assistant_count = 0 if reset else _cursor_nonnegative_int(stored, "assistant_message_count")
    checkpoint_signal = False if reset else bool(stored.get("checkpoint_signal"))
    explicit_close_reason = "" if reset else _cursor_text(stored, "explicit_close_reason")
    declared_goal = "" if reset else _cursor_text(stored, "declared_goal")
    recent_user_messages: list[str] = []
    recent_assistant_messages: list[str] = []
    complete_offset = start_offset

    with rollout_path.open("rb") as handle:
        handle.seek(start_offset)
        while True:
            line_offset = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            if not raw_line.strip():
                complete_offset = handle.tell()
                continue
            try:
                item = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                if not raw_line.endswith(b"\n"):
                    complete_offset = line_offset
                    break
                complete_offset = handle.tell()
                continue
            complete_offset = handle.tell()
            rolling_digest = hashlib.sha256(
                rolling_digest.encode("ascii", errors="ignore") + b":" + raw_line
            ).hexdigest()
            if not isinstance(item, dict):
                continue
            last_updated = item.get("timestamp") or last_updated
            item_type = item.get("type")
            payload = item.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            is_rollout_activity = False

            if item_type == "session_meta":
                candidate_id = str(payload.get("id") or "").strip()
                if candidate_id and not thread_id:
                    thread_id = candidate_id
                session_timestamp = str(payload.get("timestamp") or session_timestamp)
                candidate_cwd = str(payload.get("cwd") or "").strip()
                if candidate_cwd:
                    workspace_name = _workspace_name_from_cwd(candidate_cwd)
                source = str(payload.get("originator") or source)
                forked_from_id = str(payload.get("forked_from_id") or forked_from_id)
                agent_nickname = str(payload.get("agent_nickname") or agent_nickname)
                agent_role = str(payload.get("agent_role") or agent_role)
                declared_goal = _bounded_declared_goal(payload.get("task") or payload.get("goal") or declared_goal)
            elif item_type == "event_msg" and payload.get("type") == "user_message":
                message = str(payload.get("message") or "").strip()
                if message:
                    is_rollout_activity = True
                    user_count += 1
                    recent_user_messages.append(message)
                    checkpoint_signal = checkpoint_signal or _is_high_signal_message(message)
            elif (
                item_type == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant"
            ):
                message = extract_message_text(payload)
                if message:
                    is_rollout_activity = True
                    assistant_count += 1
                    recent_assistant_messages.append(message)
                    checkpoint_signal = checkpoint_signal or _is_high_signal_message(message)

            close_reason = _explicit_close_reason(item_type, payload)
            if close_reason:
                explicit_close_reason = close_reason
            elif is_rollout_activity:
                explicit_close_reason = ""

    if not thread_id:
        match = ROLLOUT_RE.search(rollout_path.name)
        if match:
            thread_id = match.group("thread")
    workspace_name = workspace_name or "workspace"
    anchor_start = max(0, complete_offset - ROLLOUT_CURSOR_ANCHOR_BYTES)
    anchor_digest = _file_slice_digest(rollout_path, anchor_start, complete_offset - anchor_start)
    if reset:
        prefix_length = min(ROLLOUT_CURSOR_ANCHOR_BYTES, complete_offset)
    else:
        prefix_length = min(_cursor_nonnegative_int(stored, "prefix_length"), complete_offset)
    prefix_digest = _file_slice_digest(rollout_path, 0, prefix_length)
    scan_bytes_read = complete_offset - start_offset
    fingerprint = hashlib.sha256(f"{file_identity}:{complete_offset}:{rolling_digest}".encode("utf-8")).hexdigest()[:32]
    next_cursor = {
        "version": ROLLOUT_CURSOR_VERSION,
        "file_identity": file_identity,
        "byte_offset": complete_offset,
        "rolling_digest": rolling_digest,
        "prefix_length": prefix_length,
        "prefix_digest": prefix_digest,
        "anchor_start": anchor_start,
        "anchor_digest": anchor_digest,
        "fingerprint": fingerprint,
        "thread_id": thread_id,
        "session_timestamp": session_timestamp,
        "workspace_name": workspace_name,
        "source": source,
        "forked_from_id": forked_from_id,
        "agent_nickname": agent_nickname,
        "agent_role": agent_role,
        "last_updated": last_updated or "",
        "user_message_count": user_count,
        "assistant_message_count": assistant_count,
        "checkpoint_signal": checkpoint_signal,
        "explicit_close_reason": explicit_close_reason,
        "declared_goal": declared_goal,
        "last_scan_start_offset": start_offset,
        "last_scan_bytes_read": scan_bytes_read,
    }
    summary = RolloutSummary(
        thread_id=thread_id,
        session_timestamp=session_timestamp,
        cwd=workspace_name,
        source=source,
        forked_from_id=forked_from_id,
        agent_nickname=agent_nickname,
        agent_role=agent_role,
        user_messages=_dedupe_preserve_order(recent_user_messages[-3:]),
        assistant_messages=_dedupe_preserve_order(recent_assistant_messages[-3:]),
        last_updated=last_updated,
        user_message_count=user_count,
        assistant_message_count=assistant_count,
        checkpoint_signal=checkpoint_signal,
        explicit_close_reason=explicit_close_reason,
        declared_goal=declared_goal,
        scan_start_offset=start_offset,
        scan_bytes_read=scan_bytes_read,
    )
    return summary, next_cursor


def build_closeout_payload(summary: RolloutSummary) -> dict[str, Any]:
    return _build_rollout_payload(summary, mode="closeout")


def build_checkpoint_payload(summary: RolloutSummary) -> dict[str, Any]:
    return _build_rollout_payload(summary, mode="checkpoint")


def build_session_seen_payload(summary: RolloutSummary) -> dict[str, Any]:
    session_label = summary.session_timestamp[:10] if summary.session_timestamp else "unknown-session"
    workspace_name = _workspace_name_from_cwd(summary.cwd)
    thread_id = summary.thread_id or session_label
    tags = _build_common_tags(summary, session_label, workspace_name)
    tags.extend(["kind:session-seen", "auto-session-seen", "status:active"])
    return {
        "namespace": f"project:{workspace_name}",
        "kind": "memory",
        "title": f"[[Codex]] session seen {session_label}",
        "tags": tags,
        "actor": _build_actor(summary),
        "session_id": thread_id,
        "correlation_id": summary.forked_from_id or thread_id,
        "source_app": "codex-session-seen",
        "content": (
            "record_type: session-seen\n"
            f"workspace: {workspace_name}\n"
            f"thread_id: {thread_id}\n"
            f"session_label: {session_label}\n"
            f"source: {summary.source or 'codex'}"
        ),
    }


def build_watcher_episode_begin_request(
    summary: RolloutSummary,
    *,
    workspace_key: str | None = None,
    continuation_of_run_id: str | None = None,
    continuation_key: str | None = None,
) -> dict[str, Any]:
    """Build the stable, metadata-only run request for one observed rollout."""

    resolved_workspace_key = workspace_key or _watcher_workspace_key(summary)
    thread_id = _watcher_thread_id(summary)
    idempotency_key = f"watcher:begin:{resolved_workspace_key}:{thread_id}"
    if continuation_of_run_id:
        continuation_digest = hashlib.sha256(
            str(continuation_key or continuation_of_run_id).encode("utf-8")
        ).hexdigest()[:16]
        idempotency_key = f"{idempotency_key}:continuation:{continuation_digest}"
    goal = "Observe a Codex rollout lifecycle using metadata-only watcher evidence."
    if summary.declared_goal:
        goal = f"Observe Codex rollout task metadata: {summary.declared_goal}"
    request = {
        "workspace_key": resolved_workspace_key,
        "goal": goal,
        "idempotency_key": idempotency_key,
        "agent_id": "codex-session-watcher",
        "thread_id": thread_id,
        "evidence_profile": "observational",
        "risk_level": "low",
        "provenance": _watcher_episode_provenance(thread_id),
    }
    if continuation_of_run_id:
        request["continuation_of_run_id"] = continuation_of_run_id
    return request


def build_watcher_episode_checkpoint_request(
    summary: RolloutSummary,
    fingerprint: str,
    *,
    workspace_key: str | None = None,
    lifecycle_state: str = "active",
) -> dict[str, Any]:
    """Build one active-rollout checkpoint without retaining message bodies."""

    resolved_workspace_key = workspace_key or _watcher_workspace_key(summary)
    thread_id = _watcher_thread_id(summary)
    metadata = _watcher_episode_metadata(summary, resolved_workspace_key, fingerprint)
    metadata["lifecycle_state"] = _bounded_watcher_label(lifecycle_state, default="active", max_chars=64)
    return {
        "event_type": "checkpoint",
        "summary": f"Observed a Codex rollout {metadata['lifecycle_state']} checkpoint from metadata only.",
        "idempotency_key": (
            f"watcher:{metadata['lifecycle_state']}:{resolved_workspace_key}:{thread_id}:{fingerprint}"
        ),
        "payload": metadata,
        "agent_id": "codex-session-watcher",
        "thread_id": thread_id,
        "provenance": _watcher_episode_provenance(thread_id),
    }


def build_watcher_episode_closeout_request(
    summary: RolloutSummary,
    fingerprint: str,
    *,
    workspace_key: str | None = None,
    termination_reason: str = "host_explicit_close",
) -> dict[str, Any]:
    """Build one explicit host-close request without retaining message bodies."""

    resolved_workspace_key = workspace_key or _watcher_workspace_key(summary)
    thread_id = _watcher_thread_id(summary)
    return {
        "outcome": "unverified",
        "evaluator_type": "system",
        "idempotency_key": f"watcher:closeout:{resolved_workspace_key}:{thread_id}:{fingerprint}",
        "metrics": {"rollout": _watcher_episode_metadata(summary, resolved_workspace_key, fingerprint)},
        "termination_reason": _bounded_watcher_label(
            termination_reason,
            default="host_explicit_close",
            max_chars=256,
        ),
        "provenance": _watcher_episode_provenance(thread_id),
    }


def has_checkpoint_signal(summary: RolloutSummary) -> bool:
    if summary.checkpoint_signal:
        return True
    recent_messages = [*summary.user_messages[-3:], *summary.assistant_messages[-3:]]
    if _rollout_total_count(summary) >= 4:
        return True

    text = " ".join(recent_messages).lower()
    return any(marker in text for marker in CHECKPOINT_MARKERS)


def _build_common_tags(summary: RolloutSummary, session_label: str, workspace_name: str) -> list[str]:
    tags = [
        "source:codex",
        f"session:{session_label}",
        f"workspace:{workspace_name}",
        f"project:{workspace_name}",
        f"thread:{summary.thread_id or session_label}",
    ]
    if summary.forked_from_id:
        tags.append(f"parent-thread:{summary.forked_from_id}")
    if summary.agent_nickname:
        tags.append(f"agent:{summary.agent_nickname.lower()}")
    if summary.agent_role:
        tags.append(f"agent-role:{summary.agent_role.lower()}")
    return tags


def _build_rollout_payload(summary: RolloutSummary, mode: str) -> dict[str, Any]:
    session_label = summary.session_timestamp[:10] if summary.session_timestamp else "unknown-session"
    workspace_name = _workspace_name_from_cwd(summary.cwd)
    namespace = f"project:{workspace_name}"
    if mode == "checkpoint":
        title = f"[[Codex]] checkpoint {session_label}"
        tags = _build_common_tags(summary, session_label, workspace_name)
        tags.extend(["kind:summary", "auto-checkpoint"])
        summary_text = build_checkpoint_text(summary, workspace_name)
        bullets = _build_checkpoint_bullets(summary)
        next_step = "Promote any strong decisions, fixes, or gotchas from this active checkpoint if they are likely to matter before closeout."
        slug = f"auto-checkpoint-{summary.thread_id or session_label}"
        source_app = "codex-session-checkpointer"
    else:
        title = f"[[Codex]] auto closeout {session_label}"
        tags = _build_common_tags(summary, session_label, workspace_name)
        tags.extend(["kind:summary", "auto-closeout"])
        summary_text = build_summary_text(summary, workspace_name)
        bullets = _build_closeout_bullets(summary)
        next_step = (
            "Review this auto-closeout note and promote any durable decisions into cleaner project memories if needed."
        )
        slug = f"auto-closeout-{summary.thread_id or session_label}"
        source_app = "codex-session-watcher"

    return {
        "namespace": namespace,
        "kind": "memory",
        "title": title,
        "tags": tags,
        "actor": _build_actor(summary),
        "session_id": summary.thread_id or session_label,
        "correlation_id": summary.forked_from_id or summary.thread_id or session_label,
        "source_app": source_app,
        "summary": summary_text,
        "bullets": bullets,
        "next_step": next_step,
        "session_folder": session_label,
        "slug": slug,
    }


def _build_closeout_bullets(summary: RolloutSummary) -> list[str]:
    user_samples = summary.user_messages[-3:]
    assistant_samples = summary.assistant_messages[-2:]
    bullets = [f"User asked: {truncate_line(message)}" for message in user_samples]
    bullets.extend(f"Assistant outcome: {truncate_line(message)}" for message in assistant_samples)
    if not bullets:
        bullets.append("Codex session captured without extractable messages.")
    return bullets


def _build_checkpoint_bullets(summary: RolloutSummary) -> list[str]:
    bullets: list[str] = []
    for message in _select_checkpoint_messages(summary.user_messages[-4:]):
        bullets.extend(_checkpoint_bullets_for_message(message, preferred_label="Decision"))
    for message in _select_checkpoint_messages(summary.assistant_messages[-4:]):
        bullets.extend(_checkpoint_bullets_for_message(message, preferred_label="Claim"))
    if not bullets:
        bullets.append("Active Codex rollout changed, but no durable checkpoint lines were extracted.")
    return _dedupe_preserve_order(bullets[:6])


def _select_checkpoint_messages(messages: list[str]) -> list[str]:
    selected = [message for message in messages if _is_high_signal_message(message)]
    if not selected:
        selected = messages[-2:]
    return _dedupe_preserve_order(selected[-2:])


def _is_high_signal_message(message: str) -> bool:
    normalized = " ".join(message.lower().split())
    return any(marker in normalized for marker in CHECKPOINT_MARKERS)


def _checkpoint_bullets_for_message(message: str, preferred_label: str) -> list[str]:
    sentences = _split_checkpoint_sentences(message)
    bullets: list[str] = []
    for sentence in sentences:
        label = _infer_checkpoint_label(sentence, preferred_label=preferred_label)
        bullets.append(f"{label}: {truncate_line(_strip_checkpoint_label_prefix(sentence), limit=180)}")
    return bullets


def _split_checkpoint_sentences(message: str) -> list[str]:
    compact = " ".join(message.split()).strip()
    if not compact:
        return []
    parts = re.split(r"(?<=[.!?;])\s+", compact)
    results: list[str] = []
    for part in parts:
        normalized = part.strip(" -")
        if len(normalized.split()) < 4:
            continue
        if not _is_durable_checkpoint_sentence(normalized):
            continue
        results.append(normalized)
    return results


def _infer_checkpoint_label(message: str, preferred_label: str) -> str:
    normalized = " ".join(message.lower().split())
    for label, patterns in CHECKPOINT_LABEL_PATTERNS:
        if any(pattern in normalized for pattern in patterns):
            return label
    return preferred_label


def _strip_checkpoint_label_prefix(message: str) -> str:
    normalized = message.strip()
    for prefix in ("Claim:", "Decision:", "Fix:", "Problem:", "Trigger:", "Symptom:"):
        if normalized.startswith(prefix):
            return normalized[len(prefix) :].strip()
    return normalized


def _is_durable_checkpoint_sentence(message: str) -> bool:
    normalized = " ".join(message.lower().split())
    if any(message.startswith(prefix) for prefix in EXPLICIT_CHECKPOINT_PREFIXES):
        return True
    if any(pattern in normalized for pattern in CHECKPOINT_NOISE_PATTERNS):
        return False
    if not _is_high_signal_message(normalized):
        return False
    return any(
        marker in normalized
        for marker in (
            "wrong db",
            "root cause",
            "regression",
            "contract drift",
            "canonical",
            "checkpoint sync",
            "closeout",
            "fix",
            "decision",
            "must",
            "should",
        )
    )


def _build_actor(summary: RolloutSummary) -> str:
    if summary.agent_nickname:
        return summary.agent_nickname.lower()
    return "codex"


def _workspace_name_from_cwd(cwd: str) -> str:
    normalized = cwd.strip().replace("\\", "/").rstrip("/") if cwd else ""
    if not normalized:
        return "workspace"
    return normalized.rsplit("/", 1)[-1] or "workspace"


def _watcher_workspace_name(summary: RolloutSummary) -> str:
    return _bounded_watcher_label(_workspace_name_from_cwd(summary.cwd), default="workspace", max_chars=200)


def _watcher_workspace_key(summary: RolloutSummary) -> str:
    return f"project:{_watcher_workspace_name(summary)}"


def _watcher_thread_id(summary: RolloutSummary) -> str:
    return _bounded_watcher_label(summary.thread_id, default="unknown-thread", max_chars=256)


def _watcher_episode_provenance(thread_id: str) -> dict[str, str]:
    return {
        "actor": "codex-session-watcher",
        "source_app": "codex-session-watcher",
        "source_client": "codex",
        "client_session_id": thread_id,
        "client_transport": "local-file-watcher",
    }


def _watcher_episode_metadata(
    summary: RolloutSummary,
    workspace_key: str,
    fingerprint: str,
) -> dict[str, Any]:
    user_count = _rollout_user_count(summary)
    assistant_count = _rollout_assistant_count(summary)
    workspace_basename = workspace_key.partition(":")[2] or _watcher_workspace_name(summary)
    return {
        "workspace_basename": workspace_basename,
        "workspace_key": workspace_key,
        "thread_id": _watcher_thread_id(summary),
        "source": _bounded_watcher_label(summary.source, default="codex", max_chars=128),
        "fingerprint": _bounded_watcher_label(fingerprint, default="unknown", max_chars=128),
        "last_updated": _bounded_watcher_label(summary.last_updated, default="unknown", max_chars=128),
        "session_timestamp": _bounded_watcher_label(summary.session_timestamp, default="unknown", max_chars=128),
        "user_count": user_count,
        "assistant_count": assistant_count,
        "total_count": user_count + assistant_count,
    }


def _bounded_watcher_label(value: object | None, *, default: str, max_chars: int) -> str:
    cleaned = str(value or "").strip()
    return cleaned[:max_chars] if cleaned else default


def extract_message_text(payload: dict[str, Any]) -> str:
    parts = payload.get("content", [])
    collected: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") in {"output_text", "input_text"}:
            text = (part.get("text") or "").strip()
            if text:
                collected.append(text)
    return "\n\n".join(collected).strip()


def build_summary_text(summary: RolloutSummary, workspace_name: str) -> str:
    user_count = _rollout_user_count(summary)
    assistant_count = _rollout_assistant_count(summary)
    lineage = ""
    if summary.agent_nickname:
        lineage = f" Agent `{summary.agent_nickname}` handled this rollout."
    if summary.forked_from_id:
        lineage += f" Parent thread: `{summary.forked_from_id}`."
    return (
        f"Automatic Codex closeout for workspace `{workspace_name}`. "
        f"This session included {user_count} captured user messages and {assistant_count} assistant responses."
        f"{lineage}"
    )


def build_checkpoint_text(summary: RolloutSummary, workspace_name: str) -> str:
    user_count = _rollout_user_count(summary)
    assistant_count = _rollout_assistant_count(summary)
    lineage = ""
    if summary.agent_nickname:
        lineage = f" Agent `{summary.agent_nickname}` is currently active in this rollout."
    if summary.forked_from_id:
        lineage += f" Parent thread: `{summary.forked_from_id}`."
    updated = f" Last update: `{summary.last_updated}`." if summary.last_updated else ""
    return (
        f"Automatic Codex checkpoint for workspace `{workspace_name}`. "
        f"This active rollout currently includes {user_count} captured user messages and {assistant_count} assistant responses."
        f"{lineage}{updated}"
    )


def truncate_line(text: str, limit: int = 220) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _rollout_user_count(summary: RolloutSummary) -> int:
    return max(summary.user_message_count, len(summary.user_messages))


def _rollout_assistant_count(summary: RolloutSummary) -> int:
    return max(summary.assistant_message_count, len(summary.assistant_messages))


def _rollout_total_count(summary: RolloutSummary) -> int:
    return _rollout_user_count(summary) + _rollout_assistant_count(summary)


def _bounded_declared_goal(value: object | None) -> str:
    return " ".join(str(value or "").split()).strip()[:1024]


def _explicit_close_reason(item_type: object, payload: Mapping[str, Any]) -> str:
    normalized_item_type = str(item_type or "").strip().casefold()
    payload_type = str(payload.get("type") or "").strip().casefold()
    if normalized_item_type not in EXPLICIT_CLOSE_EVENT_TYPES and payload_type not in EXPLICIT_CLOSE_EVENT_TYPES:
        return ""
    reason = _bounded_watcher_label(
        payload.get("reason") or payload.get("termination_reason"),
        default=normalized_item_type or payload_type or "host_explicit_close",
        max_chars=128,
    )
    return reason


def _rollout_file_identity(path: Path, stat: Any) -> str:
    device = int(getattr(stat, "st_dev", 0) or 0)
    inode = int(getattr(stat, "st_ino", 0) or 0)
    if inode:
        return f"device:{device}:inode:{inode}"
    return f"path:{path.resolve()}"


def _cursor_matches_file(path: Path, stat: Any, file_identity: str, cursor: Mapping[str, Any]) -> bool:
    if cursor.get("version") != ROLLOUT_CURSOR_VERSION:
        return False
    if _cursor_text(cursor, "file_identity") != file_identity:
        return False
    byte_offset = _cursor_nonnegative_int(cursor, "byte_offset")
    if int(getattr(stat, "st_size", 0) or 0) < byte_offset:
        return False
    prefix_length = _cursor_nonnegative_int(cursor, "prefix_length")
    prefix_digest = _cursor_text(cursor, "prefix_digest")
    if prefix_length and _file_slice_digest(path, 0, prefix_length) != prefix_digest:
        return False
    anchor_start = _cursor_nonnegative_int(cursor, "anchor_start")
    anchor_length = max(0, byte_offset - anchor_start)
    anchor_digest = _cursor_text(cursor, "anchor_digest")
    if anchor_length and _file_slice_digest(path, anchor_start, anchor_length) != anchor_digest:
        return False
    return True


def _file_slice_digest(path: Path, offset: int, length: int) -> str:
    digest = hashlib.sha256()
    if length <= 0:
        return digest.hexdigest()
    with Path(path).open("rb") as handle:
        handle.seek(offset)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(65_536, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _cursor_nonnegative_int(cursor: Mapping[str, Any], key: str) -> int:
    value = cursor.get(key)
    if isinstance(value, bool):
        return 0
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, normalized)


def _cursor_text(cursor: Mapping[str, Any], key: str) -> str:
    value = cursor.get(key)
    return str(value).strip() if isinstance(value, str) else ""


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
