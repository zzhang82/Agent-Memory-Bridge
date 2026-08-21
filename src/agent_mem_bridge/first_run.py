"""A read-only, product-language first-use guide for durable memory.

P2C intentionally reuses existing store, recall, Task Brief, and feedback
contracts.  It never writes a demonstration memory, exposes a recall token, or
alters ranking/promotion policy.  ``setup`` owns configuration; ``first-run``
helps a connected user experience the existing durable-memory loop.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .mcp_boundary import package_version
from .storage import MemoryStore
from .task_brief import build_task_brief_report

FIRST_RUN_SCHEMA = "memory.first_run.v2"
FIRST_RUN_BOUNDARY = "read_only_with_respect_to_user_memory_and_configuration"
RELEASE_VERSION = package_version()
# Retained stable install-contract constants. P2C intentionally removes them from
# default first-run rendering because `setup` owns connection/configuration.
PINNED_INSTALL_VERSION = "0.27.0"
GITHUB_ARCHIVE_URL = f"https://github.com/zzhang82/Agent-Memory-Bridge/archive/refs/tags/v{PINNED_INSTALL_VERSION}.zip"
RELEASE_INSTALL_GATE_NOTE = "Current package/source version is `0.28.0`."

_DEFAULT_MEMORY_PROMPTS = (
    "Before opening a PR, run <your project's test command>.",
    "Deployments to staging are triggered from <your branch or release process>.",
    "When editing <component>, preserve <your project-specific constraint>.",
)

_SUPPRESSION_LANGUAGE = {
    "superseded": "A superseded alternative was left out.",
    "unsafe": "An item that is not safe to use was left out.",
    "stale": "An out-of-date item was left out.",
    "depends_on:ineligible": "An item with an ineligible dependency was left out.",
    "depends_on:unresolved": "An item with an unresolved dependency was left out.",
    "lineage_status:degraded": "An item with incomplete history was left out.",
    "contradicted": "A contradicted item was left out.",
}


def build_first_run_report(
    store: MemoryStore,
    *,
    client: str,
    namespace: str,
    query: str,
    python_path: str | Path | None,
    cwd: str | Path | None,
    bridge_home: str | Path | None,
    config_path: str | Path | None,
    example: bool = False,
) -> dict[str, Any]:
    """Build a read-only first-use loop from existing durable-memory facts.

    The retained client/path parameters preserve parser compatibility only. They
    do not affect recall, inspect a client, write configuration, or render a
    client configuration in P2C.
    """

    del client, python_path, cwd, bridge_home, config_path, example
    cleaned_namespace = namespace.strip()
    cleaned_query = query.strip()
    if not cleaned_namespace:
        raise ValueError("namespace must not be empty")
    if not cleaned_query:
        raise ValueError("query must not be empty")

    recall_payload = store.recall(
        namespace=cleaned_namespace,
        query=cleaned_query,
        kind="memory",
        limit=3,
    )
    task_brief = build_task_brief_report(store, query=cleaned_query, namespace=cleaned_namespace)
    recall = _friendly_recall(recall_payload)
    explanation = _build_explanation(recall, task_brief)

    return {
        "schema": FIRST_RUN_SCHEMA,
        "package_version": RELEASE_VERSION,
        "namespace": cleaned_namespace,
        "query": cleaned_query,
        "mutation_boundary": FIRST_RUN_BOUNDARY,
        "boundary": {
            "mutation_allowed": False,
            "memory_write_mode": "guided_existing_store_tool_only",
            "setup_owns_connection": True,
            "feedback_policy": "shadow_only_no_memory_or_ranking_change",
            "public_mcp_surface_change": False,
        },
        "connection": {
            "state": "not_checked",
            "next": "Run `agent-memory-bridge setup` to connect safely, then use `doctor` or `verify` if connection health is unresolved.",
        },
        "remember": {
            "state": "guided_action_required" if not recall["items"] else "memory_already_available",
            "action": "Tell your connected coding agent to use the existing `store` tool for one or two real project facts. Replace the templates below with facts that are true for your project.",
            "examples": list(_DEFAULT_MEMORY_PROMPTS),
            "no_silent_write": True,
        },
        "recall": recall,
        "explanation": explanation,
        "feedback": {
            "action": "After a memory surfaces, tell your connected coding agent to record `helpful`, `misleading`, `outdated`, `not_applicable`, or `not_used` with the existing `feedback` tool.",
            "success_evidence": (
                "After the existing feedback tool succeeds, its actual response shows `stored: true`, "
                "`feedback_id: <bounded id>`, `feedback_mode: shadow_only`, and `ordering_unchanged: true`."
            ),
            "policy": "Feedback is durable evaluation evidence. It does not automatically rewrite memory or change ranking.",
            "state": "not_recorded_by_first_run",
        },
        "next_session": {
            "action": "Reopen your coding agent against the same AMB database, then ask this question again or ask a related task question.",
            "durability_claim": "The same durable memory can remain available across a fresh server session when the same database is used.",
        },
        "technical_details": _technical_details(recall_payload, task_brief, explanation),
    }


def render_first_run_markdown(report: dict[str, Any]) -> str:
    """Render a friendly explanation without internal architecture vocabulary."""

    recall = report["recall"]
    explanation = report["explanation"]
    feedback = report["feedback"]
    lines = [
        "# AMB First Run",
        "",
        "## Connection",
        "",
        report["connection"]["next"],
        "",
        "## 1. Remember",
        "",
        report["remember"]["action"],
        "Try one or two concise facts such as:",
        *[f"- {item}" for item in report["remember"]["examples"]],
        "",
        "## 2. Ask",
        "",
        f"Ask your agent: **{report['query']}**",
        "",
        "## 3. What AMB remembered",
        "",
    ]
    if recall["items"]:
        for item in recall["items"]:
            lines.extend([f"- **{item['title']}** — {item['summary']}"])
    else:
        lines.extend(
            [
                "No suitable memory surfaced yet.",
                "",
                "Next step: remember one useful project fact with your connected coding agent, then ask the question again.",
            ]
        )

    lines.extend(["", "## 4. Why this appeared", ""])
    if explanation["reasons"]:
        lines.extend(f"- {reason}" for reason in explanation["reasons"])
    else:
        lines.append("No memory surfaced, so there is no selection explanation yet.")
    if explanation["not_used"]:
        lines.extend(["", "What was deliberately not used:"])
        lines.extend(f"- {reason}" for reason in explanation["not_used"])

    lines.extend(
        [
            "",
            "## 5. Feedback",
            "",
            feedback["action"],
            "",
            "What successful feedback looks like:",
            feedback["success_evidence"],
            feedback["policy"],
            "",
            "## 6. Next session",
            "",
            report["next_session"]["action"],
            report["next_session"]["durability_claim"],
            "",
            "## Technical details",
            "",
            f"- namespace: `{report['namespace']}`",
            f"- recall results: `{report['technical_details']['result_count']}`",
            f"- retrieval mode: `{report['technical_details']['retrieval_mode']}`",
        ]
    )
    for item in report["technical_details"]["selected"]:
        lines.append(
            f"- selected memory: `{item['memory_id']}`; rank `{item['rank']}`; reason codes: `{', '.join(item['reason_codes']) or 'none'}`"
        )
    for reason in report["technical_details"]["suppressed_reason_codes"]:
        lines.append(f"- excluded reason code: `{reason}`")
    return "\n".join(lines)


def _friendly_recall(payload: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for rank, item in enumerate(payload.get("items") or [], start=1):
        if not isinstance(item, dict):
            continue
        memory_id = str(item.get("id") or "")
        if not memory_id:
            continue
        content = str(item.get("content") or "")
        title = _friendly_title(item.get("title"), content, rank)
        items.append(
            {
                "title": title,
                "summary": _summarize_content(content),
                "rank": rank,
                "memory_id": memory_id,
                "namespace": str(item.get("namespace") or ""),
            }
        )
    return {"count": len(items), "items": items}


def _build_explanation(recall: dict[str, Any], task_brief: dict[str, Any]) -> dict[str, Any]:
    used_by_id = {
        str(item.get("source_record_id") or ""): item for item in (task_brief.get("sections") or {}).get("used") or []
    }
    reasons: list[str] = []
    selected_reasons: dict[str, list[str]] = {}
    if recall["items"]:
        reasons.extend(
            [
                "It was returned by the existing durable-memory recall for this question.",
                "It belongs to the selected project namespace.",
            ]
        )
        for item in recall["items"]:
            brief_item = used_by_id.get(item["memory_id"])
            codes = [str(code) for code in (brief_item or {}).get("reason_codes") or []]
            selected_reasons[item["memory_id"]] = codes
            if brief_item is not None:
                reasons.append("It also passed the existing task eligibility checks.")
                break
    not_used = _not_used_language(task_brief)
    return {"reasons": _dedupe(reasons), "not_used": not_used, "selected_reason_codes": selected_reasons}


def _not_used_language(task_brief: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    sections = task_brief.get("sections") or {}
    for section_name in ("ignored", "needs_review"):
        for item in sections.get(section_name) or []:
            for code in item.get("reason_codes") or []:
                code_text = str(code)
                message = _SUPPRESSION_LANGUAGE.get(code_text)
                if message is None and code_text.startswith("procedure_status:unsafe"):
                    message = _SUPPRESSION_LANGUAGE["unsafe"]
                if message is None and code_text.startswith("validity:stale"):
                    message = _SUPPRESSION_LANGUAGE["stale"]
                if message is not None:
                    messages.append(message)
    return _dedupe(messages)[:3]


def _technical_details(
    recall_payload: dict[str, Any],
    task_brief: dict[str, Any],
    explanation: dict[str, Any],
) -> dict[str, Any]:
    selected = [
        {
            "memory_id": memory_id,
            "rank": rank,
            "reason_codes": explanation["selected_reason_codes"].get(memory_id, []),
        }
        for rank, memory_id in enumerate(
            [str(item.get("id") or "") for item in recall_payload.get("items") or [] if isinstance(item, dict)],
            start=1,
        )
        if memory_id
    ]
    suppressed_codes = [
        str(code)
        for section_name in ("ignored", "needs_review")
        for item in (task_brief.get("sections") or {}).get(section_name) or []
        for code in item.get("reason_codes") or []
    ]
    retrieval = recall_payload.get("retrieval") or {}
    return {
        "result_count": int(recall_payload.get("count") or 0),
        "retrieval_mode": str(retrieval.get("mode") or "unknown"),
        "selected": selected,
        "suppressed_reason_codes": _dedupe(suppressed_codes)[:10],
        "receipt_exposed": False,
        "feedback_policy": "shadow_only_no_memory_or_ranking_change",
    }


def _friendly_title(value: object, content: str, rank: int) -> str:
    title = str(value or "").strip()
    if title:
        return title[:120]
    summary = _summarize_content(content)
    return summary[:120] if summary else f"Remembered item {rank}"


def _summarize_content(content: str) -> str:
    compact = re.sub(r"\s+", " ", content).strip()
    if len(compact) <= 240:
        return compact
    return f"{compact[:237].rstrip()}..."


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
