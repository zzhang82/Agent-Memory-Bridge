from __future__ import annotations

from pathlib import Path
from typing import Any

from .memory_cli import _SUPERSEDED_SQL, display_memory_label, resolve_default_namespace, truncate_on_word
from .repository import MEMORY_ROW_SELECT, MemoryRow
from .storage import MemoryStore

INDEX_SCHEMA = "memory.compiled_index.v1"
MIN_INDEX_LINES = 10
MAX_INDEX_LINES = 200
MIN_INDEX_BYTES = 1024
MAX_INDEX_BYTES = 25_600  # 25 KB


def compile_memory_index(
    store: MemoryStore,
    *,
    namespace: str | None = None,
    max_lines: int = MAX_INDEX_LINES,
    max_bytes: int = MAX_INDEX_BYTES,
) -> str:
    """Compile an operator-visible, compact Markdown index of confirmed memories.

    Enforces strict size ceilings (<= 200 lines and <= 25 KB). Lists record
    identifiers, titles/claims, and domain pointers to solve the 'unknown unknowns'
    discovery problem at session start without dumping raw record bodies.
    """
    target_namespace = resolve_default_namespace(namespace)
    line_limit = max(MIN_INDEX_LINES, min(max_lines, MAX_INDEX_LINES))
    byte_limit = max(MIN_INDEX_BYTES, min(max_bytes, MAX_INDEX_BYTES))

    with store._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {MEMORY_ROW_SELECT}
            FROM memories
            WHERE namespace = ?
              AND kind = 'memory'
              AND COALESCE(is_learning_candidate, 0) = 0
              AND NOT {_SUPERSEDED_SQL}
            ORDER BY created_at DESC
            LIMIT 500
            """,
            (target_namespace,),
        ).fetchall()
        parsed = [MemoryRow.from_sqlite(row) for row in rows]

    # Partition by record type
    decisions: list[MemoryRow] = []
    constraints: list[MemoryRow] = []
    gotchas: list[MemoryRow] = []
    others: list[MemoryRow] = []

    for mem in parsed:
        rt = (mem.record_type or "").lower().strip()
        tags = set(mem.tags)
        if rt == "decision" or "kind:decision" in tags:
            decisions.append(mem)
        elif rt == "constraint" or "kind:constraint" in tags:
            constraints.append(mem)
        elif rt == "gotcha" or "kind:gotcha" in tags or "gotcha" in tags:
            gotchas.append(mem)
        else:
            others.append(mem)

    header_lines = [
        f"# Memory Index: `{target_namespace}`",
        "",
        "Compact table of contents for session start. Use targeted `recall` for full details.",
        "",
    ]

    body_lines: list[str] = []

    def _format_item(m: MemoryRow) -> str:
        label = truncate_on_word(display_memory_label(m.title, m.content, fallback=m.id), 72)
        domain_tags = [t.partition(":")[2] for t in m.tags if t.startswith("domain:") or t.startswith("topic:")]
        tag_str = f" [{', '.join(domain_tags[:3])}]" if domain_tags else ""
        return f"- `{m.id}`: {label}{tag_str}"

    sections = [
        ("## Decisions", decisions),
        ("## Constraints", constraints),
        ("## Gotchas & Guidelines", gotchas),
        ("## General Records", others),
    ]

    total_records = len(parsed)
    rendered_records = 0

    for title, group in sections:
        if not group:
            continue
        group_lines = [title, ""]
        for m in group:
            group_lines.append(_format_item(m))
            rendered_records += 1
        group_lines.append("")
        body_lines.extend(group_lines)

    # Reserve lines for footer, the omission notice, and any limit-adjustment note.
    adjustments = _limit_adjustments(max_lines, line_limit, max_bytes, byte_limit)
    max_body_lines = max(0, line_limit - len(header_lines) - 4 - len(adjustments))
    omitted = 0

    if len(body_lines) > max_body_lines:
        cutoff = max_body_lines
        # Adjust cutoff so we don't end on a section header.
        # cutoff == 0 must not index body_lines[-1].
        if cutoff > 0 and body_lines[cutoff - 1].startswith("##"):
            cutoff -= 1
        omitted = total_records - sum(1 for line in body_lines[:cutoff] if line.startswith("- `"))
        body_lines = body_lines[:cutoff]

    footer_lines = [f"<!-- {adjustment} -->" for adjustment in adjustments]
    if omitted > 0:
        footer_lines.extend(
            [
                f"<!-- {omitted} additional records omitted; use targeted recall to inspect specific topics -->",
                "",
            ]
        )
    footer_lines.append(f"<!-- index total: {rendered_records} records, namespace: {target_namespace} -->\n")

    full_text = "\n".join(header_lines + body_lines + footer_lines)

    # Enforce byte ceiling without slicing a multibyte character in half.
    encoded = full_text.encode("utf-8")
    if len(encoded) > byte_limit:
        notice = f"<!-- truncated to stay within {byte_limit} bytes -->"
        adjustment_lines = [f"<!-- {adjustment} -->" for adjustment in adjustments]
        tail = [line for line in footer_lines if line not in adjustment_lines]
        tail.append(notice)
        protected_tail = adjustment_lines + tail
        body = list(body_lines)
        while body and len(("\n".join(header_lines + body + protected_tail) + "\n").encode("utf-8")) > byte_limit:
            body.pop()
        full_text = "\n".join(header_lines + body + protected_tail) + "\n"

    return full_text


def write_memory_index(content: str, out_path: Path) -> Path:
    """Safely write compiled index content to destination path."""
    resolved = out_path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp_path = resolved.with_name(f"{resolved.name}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(resolved)
    return resolved


def _limit_adjustments(max_lines: int, line_limit: int, max_bytes: int, byte_limit: int) -> list[str]:
    """Describe requested limits that were clamped, as HTML-comment bodies."""

    adjustments: list[str] = []
    for name, requested, effective, floor, ceiling in (
        ("max_lines", max_lines, line_limit, MIN_INDEX_LINES, MAX_INDEX_LINES),
        ("max_bytes", max_bytes, byte_limit, MIN_INDEX_BYTES, MAX_INDEX_BYTES),
    ):
        if requested < floor:
            adjustments.append(f"requested {name}={requested} raised to minimum {effective}")
        elif requested > ceiling:
            adjustments.append(f"requested {name}={requested} lowered to maximum {effective}")
    return adjustments


def build_memory_index_metadata(
    content: str,
    *,
    namespace: str,
    max_lines: int = MAX_INDEX_LINES,
    max_bytes: int = MAX_INDEX_BYTES,
) -> dict[str, Any]:
    """Return machine-readable metadata for the compiled index.

    ``max_lines`` and ``max_bytes`` are the requested limits. The effective
    limits are what compilation actually enforced, and ``size_capped`` is judged
    against those effective limits.
    """
    lines = content.splitlines()
    byte_count = len(content.encode("utf-8"))
    record_count = sum(1 for line in lines if line.startswith("- `"))
    effective_max_lines = max(MIN_INDEX_LINES, min(max_lines, MAX_INDEX_LINES))
    effective_max_bytes = max(MIN_INDEX_BYTES, min(max_bytes, MAX_INDEX_BYTES))
    limits_adjusted = [
        note.removeprefix("requested ")
        for note in _limit_adjustments(max_lines, effective_max_lines, max_bytes, effective_max_bytes)
    ]
    return {
        "schema": INDEX_SCHEMA,
        "namespace": namespace,
        "line_count": len(lines),
        "byte_count": byte_count,
        "record_count": record_count,
        "requested_max_lines": max_lines,
        "effective_max_lines": effective_max_lines,
        "requested_max_bytes": max_bytes,
        "effective_max_bytes": effective_max_bytes,
        "limits_adjusted": limits_adjusted,
        "size_capped": len(lines) <= effective_max_lines and byte_count <= effective_max_bytes,
        "content": content,
    }
