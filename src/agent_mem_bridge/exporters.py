from __future__ import annotations

import json
from typing import Any


def render_export(items: list[dict[str, Any]], namespace: str, format: str) -> str:
    if format == "json":
        return json.dumps({"namespace": namespace, "count": len(items), "items": items}, indent=2, ensure_ascii=False)
    if format == "text":
        return render_text_export(items, namespace=namespace)
    return render_markdown_export(items, namespace=namespace)


def render_text_export(items: list[dict[str, Any]], namespace: str) -> str:
    lines = [f"namespace: {namespace}", f"count: {len(items)}"]
    if items:
        lines.append("")
    for item in items:
        lines.extend(
            [
                f"id: {item['id']}",
                f"title: {item.get('title') or ''}",
                f"kind: {item['kind']}",
                f"signal_status: {item.get('signal_status') or ''}",
                f"created_at: {item['created_at']}",
                f"tags: {', '.join(item.get('tags', []))}",
                "content:",
                item["content"],
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def render_markdown_export(items: list[dict[str, Any]], namespace: str) -> str:
    lines = [f"# Memory Export: {namespace}", "", f"- Count: {len(items)}"]
    for item in items:
        lines.extend(
            [
                "",
                f"## {item.get('title') or item['id']}",
                "",
                f"- ID: `{item['id']}`",
                f"- Kind: `{item['kind']}`",
                f"- Signal Status: `{item.get('signal_status') or 'n/a'}`",
                f"- Created: `{item['created_at']}`",
                f"- Tags: {', '.join(f'`{tag}`' for tag in item.get('tags', [])) or '(none)'}",
                "",
                "```text",
                item["content"],
                "```",
            ]
        )
    return "\n".join(lines).rstrip()
