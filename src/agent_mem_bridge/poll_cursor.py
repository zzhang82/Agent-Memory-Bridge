from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

CURSOR_PREFIX = "amb2"
LEGACY_CURSOR_PREFIX = "amb1"


@dataclass(frozen=True, slots=True)
class PollCursor:
    namespace: str
    sequence: int
    database_epoch: str | None = None


def encode_poll_cursor(*, namespace: str, sequence: int, database_epoch: str) -> str:
    cleaned_epoch = database_epoch.strip()
    if not cleaned_epoch:
        raise ValueError("database_epoch must not be empty")
    payload = json.dumps(
        {"database_epoch": cleaned_epoch, "namespace": namespace, "sequence": sequence},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    checksum = hashlib.sha256(payload).hexdigest()[:16]
    return f"{CURSOR_PREFIX}.{encoded}.{checksum}"


def decode_poll_cursor(value: str) -> PollCursor | None:
    prefix, separator, remainder = value.partition(".")
    if not separator or prefix not in {CURSOR_PREFIX, LEGACY_CURSOR_PREFIX}:
        return None
    encoded, separator, checksum = remainder.rpartition(".")
    if not separator or not encoded or not checksum:
        raise ValueError("invalid since cursor: malformed opaque cursor")
    padding = "=" * (-len(encoded) % 4)
    try:
        payload = base64.urlsafe_b64decode(encoded + padding)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid since cursor: malformed opaque cursor") from exc
    if hashlib.sha256(payload).hexdigest()[:16] != checksum:
        raise ValueError("invalid since cursor: checksum mismatch")
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid since cursor: malformed opaque cursor") from exc
    if not isinstance(parsed, dict):
        raise ValueError("invalid since cursor: malformed opaque cursor")
    namespace = parsed.get("namespace")
    sequence = parsed.get("sequence", parsed.get("rowid"))
    database_epoch = parsed.get("database_epoch")
    if not isinstance(namespace, str) or not namespace.strip() or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("invalid since cursor: malformed opaque cursor")
    if prefix == CURSOR_PREFIX and (not isinstance(database_epoch, str) or not database_epoch.strip()):
        raise ValueError("invalid since cursor: missing database epoch")
    if database_epoch is not None and not isinstance(database_epoch, str):
        raise ValueError("invalid since cursor: malformed database epoch")
    return PollCursor(
        namespace=namespace,
        sequence=sequence,
        database_epoch=database_epoch.strip() if isinstance(database_epoch, str) else None,
    )
