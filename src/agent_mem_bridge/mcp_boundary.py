from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

SERVER_NAME = "agent-memory-bridge"
SERVER_TITLE = "Agent Memory Bridge"
SERVER_DESCRIPTION = "Persistent engineering memory for coding agents over MCP"
_GENERIC_SDK_CLIENT_NAMES = frozenset({"mcp"})


def package_version() -> str:
    try:
        return version("agent-memory-bridge")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if pyproject_path.exists():
            with pyproject_path.open("rb") as handle:
                return str(tomllib.load(handle)["project"]["version"])
        return "0.0.0"


def context_source_client(ctx: Any | None) -> str | None:
    if ctx is None:
        return None
    try:
        client_params = ctx.session.client_params
    except (AttributeError, ValueError):
        return None
    client_info = getattr(client_params, "client_info", None)
    client_name = _optional_text(getattr(client_info, "name", None))
    if client_name is None or client_name.casefold() in _GENERIC_SDK_CLIENT_NAMES:
        return None
    return client_name


def with_context_source_client(
    provenance: dict[str, str | None] | None,
    ctx: Any | None,
) -> dict[str, str | None] | None:
    source_client = context_source_client(ctx)
    if source_client is None:
        return provenance
    if provenance is None:
        return {"source_client": source_client}
    if _optional_text(provenance.get("source_client")) is not None:
        return provenance
    enriched = dict(provenance)
    enriched["source_client"] = source_client
    return enriched


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None
