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
        relation_parts = [
            f"{name}={', '.join(values)}"
            for name, values in (item.get("relations") or {}).items()
            if values
        ]
        lines.extend(
            [
                f"id: {item['id']}",
                f"title: {item.get('title') or ''}",
                f"kind: {item['kind']}",
                f"signal_status: {item.get('signal_status') or ''}",
                f"created_at: {item['created_at']}",
                f"source_client: {item.get('source_client') or ''}",
                f"source_model: {item.get('source_model') or ''}",
                f"client_session_id: {item.get('client_session_id') or ''}",
                f"client_workspace: {item.get('client_workspace') or ''}",
                f"client_transport: {item.get('client_transport') or ''}",
                f"relations: {'; '.join(relation_parts) if relation_parts else ''}",
                f"valid_from: {item.get('valid_from') or ''}",
                f"valid_until: {item.get('valid_until') or ''}",
                f"validity_status: {item.get('validity_status') or ''}",
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
        relation_parts = [
            f"`{name}` -> {', '.join(f'`{value}`' for value in values)}"
            for name, values in (item.get("relations") or {}).items()
            if values
        ]
        lines.extend(
            [
                "",
                f"## {item.get('title') or item['id']}",
                "",
                f"- ID: `{item['id']}`",
                f"- Kind: `{item['kind']}`",
                f"- Signal Status: `{item.get('signal_status') or 'n/a'}`",
                f"- Created: `{item['created_at']}`",
                f"- Source Client: `{item.get('source_client') or 'n/a'}`",
                f"- Source Model: `{item.get('source_model') or 'n/a'}`",
                f"- Client Session: `{item.get('client_session_id') or 'n/a'}`",
                f"- Client Workspace: `{item.get('client_workspace') or 'n/a'}`",
                f"- Client Transport: `{item.get('client_transport') or 'n/a'}`",
                f"- Relations: {'; '.join(relation_parts) if relation_parts else '(none)'}",
                f"- Valid From: `{item.get('valid_from') or 'n/a'}`",
                f"- Valid Until: `{item.get('valid_until') or 'n/a'}`",
                f"- Validity Status: `{item.get('validity_status') or 'n/a'}`",
                f"- Tags: {', '.join(f'`{tag}`' for tag in item.get('tags', [])) or '(none)'}",
                "",
                "```text",
                item["content"],
                "```",
            ]
        )
    return "\n".join(lines).rstrip()
