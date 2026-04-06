from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


SIGNAL_STATUSES = {"pending", "claimed", "acked", "expired"}


@dataclass(frozen=True, slots=True)
class SignalSnapshot:
    kind: str
    signal_status: str | None
    claimed_by: str | None
    claimed_at: str | None
    lease_expires_at: str | None
    expires_at: str | None
    acknowledged_at: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any] | Any) -> "SignalSnapshot":
        return cls(
            kind=str(row["kind"]),
            signal_status=_clean_optional(row["signal_status"]),
            claimed_by=_clean_optional(row["claimed_by"]),
            claimed_at=_clean_optional(row["claimed_at"]),
            lease_expires_at=_clean_optional(row["lease_expires_at"]),
            expires_at=_clean_optional(row["expires_at"]),
            acknowledged_at=_clean_optional(row["acknowledged_at"]),
        )


def resolve_signal_expiry(*, expires_at: str | None, ttl_seconds: int | None, now: datetime | None = None) -> str | None:
    if expires_at and expires_at.strip():
        normalized = _parse_iso_utc(expires_at.strip())
        if normalized is None:
            raise ValueError("expires_at must be a valid ISO-8601 datetime")
        return normalized.isoformat()
    if ttl_seconds is None:
        return None
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be greater than 0")
    anchor = now or datetime.now(UTC)
    return (anchor + timedelta(seconds=ttl_seconds)).isoformat()


def effective_signal_status(snapshot: SignalSnapshot, now: datetime | None = None) -> str | None:
    if snapshot.kind != "signal":
        return None
    current = now or datetime.now(UTC)
    if snapshot.acknowledged_at or snapshot.signal_status == "acked":
        return "acked"
    if _is_expired(snapshot.expires_at, current):
        return "expired"
    if _is_expired(snapshot.lease_expires_at, current):
        return "pending"
    if snapshot.signal_status in {"claimed", "pending"}:
        return snapshot.signal_status
    if snapshot.signal_status == "expired":
        return "expired"
    return "pending"


def is_signal_claimable(snapshot: SignalSnapshot, *, consumer: str | None = None, now: datetime | None = None) -> bool:
    status = effective_signal_status(snapshot, now=now)
    if status in {"acked", "expired"}:
        return False
    if status == "pending":
        return True
    if status == "claimed" and consumer and snapshot.claimed_by == consumer:
        return True
    return False


def normalize_signal_status_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized not in SIGNAL_STATUSES:
        raise ValueError(f"signal_status must be one of {sorted(SIGNAL_STATUSES)}")
    return normalized


def _is_expired(raw_value: str | None, now: datetime) -> bool:
    parsed = _parse_iso_utc(raw_value)
    return parsed is not None and parsed <= now


def _parse_iso_utc(raw_value: str | None) -> datetime | None:
    cleaned = _clean_optional(raw_value)
    if cleaned is None:
        return None
    candidate = cleaned.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
