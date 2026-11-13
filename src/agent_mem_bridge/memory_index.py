from __future__ import annotations

from pathlib import Path
from typing import Any

from .memory_cli import resolve_default_namespace
from .repository import MEMORY_ROW_SELECT, MemoryRow
from .storage import MemoryStore

INDEX_SCHEMA = "memory.compiled_index.v1"
MAX_INDEX_LINES = 200
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
    line_limit = max(10, min(max_lines, MAX_INDEX_LINES))
    byte_limit = max(1024, min(max_bytes, MAX_INDEX_BYTES))

    with store._connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {MEMORY_ROW_SELECT}
            FROM memories
            WHERE namespace = ?
              AND kind = 'memory'
              AND COALESCE(is_learning_candidate, 0) = 0
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
        label = (m.title or m.content.splitlines()[0] if m.content else m.id).strip()
        if len(label) > 72:
            label = label[:69] + "..."
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

    # Reserve lines for footer/omission notice
    max_body_lines = line_limit - len(header_lines) - 4
    omitted = 0

    if len(body_lines) > max_body_lines:
        cutoff = max_body_lines
        # Adjust cutoff so we don't end on a section header
        if body_lines[cutoff - 1].startswith("##"):
            cutoff -= 1
        omitted = total_records - sum(1 for line in body_lines[:cutoff] if line.startswith("- `"))
        body_lines = body_lines[:cutoff]

    footer_lines = []
    if omitted > 0:
        footer_lines.extend(
            [
                f"<!-- {omitted} additional records omitted; use targeted recall to inspect specific topics -->",
                "",
            ]
        )
    footer_lines.append(f"<!-- index total: {rendered_records} records, namespace: {target_namespace} -->\n")

    full_text = "\n".join(header_lines + body_lines + footer_lines)

    # Enforce byte ceiling
    encoded = full_text.encode("utf-8")
    if len(encoded) > byte_limit:
        # Binary-safe truncation back to clean line
        truncated = encoded[:byte_limit].decode("utf-8", errors="ignore")
        lines = truncated.splitlines()
        if len(lines) > 2:
            lines = lines[:-1]
        lines.append(f"<!-- truncated to stay within {byte_limit} bytes -->")
        full_text = "\n".join(lines) + "\n"

    return full_text


def write_memory_index(content: str, out_path: Path) -> Path:
    """Safely write compiled index content to destination path."""
    resolved = out_path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp_path = resolved.with_name(f"{resolved.name}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(resolved)
    return resolved


def build_memory_index_metadata(content: str, *, namespace: str) -> dict[str, Any]:
    """Return machine-readable metadata for the compiled index."""
    lines = content.splitlines()
    byte_count = len(content.encode("utf-8"))
    record_count = sum(1 for line in lines if line.startswith("- `"))
    return {
        "schema": INDEX_SCHEMA,
        "namespace": namespace,
        "line_count": len(lines),
        "byte_count": byte_count,
        "record_count": record_count,
        "size_capped": len(lines) <= MAX_INDEX_LINES and byte_count <= MAX_INDEX_BYTES,
        "content": content,
    }
