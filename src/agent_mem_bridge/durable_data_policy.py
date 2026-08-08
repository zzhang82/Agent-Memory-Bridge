from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_FORBIDDEN_COMPACT_KEYS = frozenset(
    {
        "rawcot",
        "chainofthought",
        "transcript",
        "messages",
        "hiddenreasoning",
        "reasoningtext",
        "reasoning",
        "analysis",
        "thoughtprocess",
        "recallreceipt",
        "receipttoken",
    }
)
_FORBIDDEN_PREFIXES = ("raw", "transcript", "reasoning", "thoughtprocess", "hiddenreasoning", "chainofthought")
_SAFE_COMPACT_KEYS = frozenset({"analysisdigest"})
_KEY_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")
_RECEIPT_SHAPED_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])v2\.[A-Za-z0-9_-]{32,32768}\.[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])"
)
_RECEIPT_SHAPED_VALUE_MARKER = "<receipt-shaped value>"


def forbidden_durable_structured_field(value: Any) -> str | None:
    """Return the first privacy-prohibited structured field or value, if present.

    The policy applies to durable JSON structures only.  It recognizes case and
    separator variants while allowing factual metadata such as ``analysis_digest``.
    Receipt-shaped token values are rejected without returning the token itself.
    """

    if isinstance(value, str):
        if _RECEIPT_SHAPED_VALUE_RE.search(value):
            return _RECEIPT_SHAPED_VALUE_MARKER
    elif isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            if _is_forbidden_key(normalize_durable_key(key)):
                return key
            forbidden = forbidden_durable_structured_field(nested)
            if forbidden is not None:
                return forbidden
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            forbidden = forbidden_durable_structured_field(item)
            if forbidden is not None:
                return forbidden
    return None


def require_durable_structured_data(value: Any, *, subject: str) -> None:
    """Fail closed when durable JSON would retain hidden reasoning or receipts."""

    field = forbidden_durable_structured_field(value)
    if field == _RECEIPT_SHAPED_VALUE_MARKER:
        raise ValueError(f"{subject} rejects receipt-shaped value")
    if field is not None:
        raise ValueError(f"{subject} rejects field: {field}")


def require_durable_text(value: str, *, subject: str) -> None:
    """Fail closed when durable text is shaped like a recall receipt token."""

    if _RECEIPT_SHAPED_VALUE_RE.search(value):
        raise ValueError(f"{subject} rejects receipt-shaped value")


def normalize_durable_key(value: str) -> str:
    """Canonicalize a durable structured key across case and separators."""

    return _KEY_SEPARATOR_RE.sub("", value.casefold())


def _is_forbidden_key(normalized: str) -> bool:
    if normalized in _FORBIDDEN_COMPACT_KEYS:
        return True
    if normalized in _SAFE_COMPACT_KEYS:
        return False
    return normalized.startswith(_FORBIDDEN_PREFIXES) or normalized.startswith("analysis")
