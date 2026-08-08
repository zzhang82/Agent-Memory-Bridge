from __future__ import annotations

from collections.abc import Mapping

from .durable_data_policy import require_durable_text

PROVENANCE_FIELDS = (
    "session_id",
    "actor",
    "correlation_id",
    "source_app",
    "source_client",
    "source_model",
    "client_session_id",
    "client_workspace",
    "client_transport",
)

PROVENANCE_LENGTH_LIMITS = {
    "session_id": 256,
    "actor": 128,
    "correlation_id": 256,
    "source_app": 128,
    "source_client": 128,
    "source_model": 128,
    "client_session_id": 256,
    "client_workspace": 512,
    "client_transport": 64,
}


def normalize_provenance_value(field: str, value: object | None) -> str | None:
    """Normalize one declared provenance value and reject ambiguous truncation."""

    if field not in PROVENANCE_LENGTH_LIMITS:
        raise ValueError(f"unsupported provenance field: {field}")
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    limit = PROVENANCE_LENGTH_LIMITS[field]
    if len(cleaned) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    require_durable_text(cleaned, subject="durable provenance")
    return cleaned


def normalize_provenance_mapping(
    provenance: Mapping[str, object | None] | None,
) -> dict[str, str] | None:
    """Return supported, bounded provenance while preserving unknown-field behavior."""

    if provenance is None:
        return None
    cleaned = {
        field: value
        for field in PROVENANCE_FIELDS
        if (value := normalize_provenance_value(field, provenance.get(field))) is not None
    }
    return cleaned or None
