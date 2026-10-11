from __future__ import annotations


def canonical_namespace(namespace: str) -> str:
    cleaned = namespace.strip()
    if cleaned.casefold().startswith("project:"):
        return f"project:{cleaned[8:].casefold()}"
    return cleaned
