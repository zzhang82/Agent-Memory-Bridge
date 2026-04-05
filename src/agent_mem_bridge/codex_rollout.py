from __future__ import annotations

import json
import re
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
    ("Problem", ("problem:", "wrong db", "bug", "error", "issue", "drift", "regression", "missing", "fails", "failure")),
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

    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        item = json.loads(raw_line)
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
            continue

        if item_type == "event_msg" and payload.get("type") == "user_message":
            message = (payload.get("message") or "").strip()
            if message:
                user_messages.append(message)
            continue

        if item_type == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant":
            text = extract_message_text(payload)
            if text:
                assistant_messages.append(text)

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
    )


def build_closeout_payload(summary: RolloutSummary) -> dict[str, Any]:
    return _build_rollout_payload(summary, mode="closeout")


def build_checkpoint_payload(summary: RolloutSummary) -> dict[str, Any]:
    return _build_rollout_payload(summary, mode="checkpoint")


def build_session_seen_payload(summary: RolloutSummary) -> dict[str, Any]:
    session_label = summary.session_timestamp[:10] if summary.session_timestamp else "unknown-session"
    workspace_name = Path(summary.cwd).name if summary.cwd else "workspace"
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


def has_checkpoint_signal(summary: RolloutSummary) -> bool:
    recent_messages = [*summary.user_messages[-3:], *summary.assistant_messages[-3:]]
    if len(recent_messages) >= 4:
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
    workspace_name = Path(summary.cwd).name if summary.cwd else "workspace"
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
        next_step = "Review this auto-closeout note and promote any durable decisions into cleaner project memories if needed."
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
            return normalized[len(prefix):].strip()
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
    return "cole"


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
    user_count = len(summary.user_messages)
    assistant_count = len(summary.assistant_messages)
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
    user_count = len(summary.user_messages)
    assistant_count = len(summary.assistant_messages)
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


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
