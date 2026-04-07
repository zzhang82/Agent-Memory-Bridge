from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable


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


def select_claimable_signal(
    conn: sqlite3.Connection,
    *,
    row_select_sql: str,
    namespace: str,
    signal_id: str | None,
    tags_any: list[str] | None,
    correlation_id: str | None,
    consumer: str,
    now: datetime,
    build_tag_filter: Callable[[list[str] | None, str], tuple[str, list[str]]],
) -> sqlite3.Row | None:
    clauses = ["namespace = ?", "kind = 'signal'"]
    params: list[Any] = [namespace]
    if signal_id:
        clauses.append("id = ?")
        params.append(signal_id)
    if correlation_id:
        clauses.append("correlation_id = ?")
        params.append(correlation_id)
    tag_filter_sql, tag_params = build_tag_filter(tags_any, "")
    if tag_filter_sql:
        clauses.append(tag_filter_sql)
        params.extend(tag_params)

    rows = conn.execute(
        f"""
        SELECT
            {row_select_sql}
        FROM memories
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at ASC
        LIMIT 50
        """,
        params,
    ).fetchall()
    for row in rows:
        if is_signal_claimable(SignalSnapshot.from_row(row), consumer=consumer, now=now):
            return row
    return None


def claim_signal_entry(
    *,
    store: Any,
    namespace: str,
    consumer: str,
    lease_seconds: int,
    signal_id: str | None,
    tags_any: list[str] | None,
    correlation_id: str | None,
    row_select_sql: str,
    build_tag_filter: Callable[[list[str] | None, str], tuple[str, list[str]]],
    fetch_row_by_id: Callable[[sqlite3.Connection, str], sqlite3.Row | None],
    row_to_item: Callable[[sqlite3.Row], dict[str, Any]],
) -> dict[str, Any]:
    cleaned_namespace = namespace.strip()
    cleaned_consumer = consumer.strip()
    cleaned_signal_id = signal_id.strip() if signal_id else None
    if not cleaned_namespace:
        raise ValueError("namespace must not be empty")
    if not cleaned_consumer:
        raise ValueError("consumer must not be empty")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be greater than 0")

    now = datetime.now(UTC)
    claimed_at = now.isoformat()
    lease_expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()

    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        candidate = select_claimable_signal(
            conn,
            row_select_sql=row_select_sql,
            namespace=cleaned_namespace,
            signal_id=cleaned_signal_id,
            tags_any=tags_any,
            correlation_id=correlation_id,
            consumer=cleaned_consumer,
            now=now,
            build_tag_filter=build_tag_filter,
        )
        if candidate is None:
            conn.rollback()
            store._log(
                "claim_signal",
                {
                    "namespace": cleaned_namespace,
                    "consumer": cleaned_consumer,
                    "claimed": False,
                    "signal_id": cleaned_signal_id,
                },
            )
            return {
                "claimed": False,
                "signal_id": cleaned_signal_id,
                "namespace": cleaned_namespace,
                "consumer": cleaned_consumer,
                "item": None,
            }

        conn.execute(
            """
            UPDATE memories
            SET signal_status = 'claimed',
                claimed_by = ?,
                claimed_at = ?,
                lease_expires_at = ?,
                acknowledged_at = NULL
            WHERE id = ?
            """,
            (cleaned_consumer, claimed_at, lease_expires_at, candidate["id"]),
        )
        conn.commit()
        refreshed = fetch_row_by_id(conn, candidate["id"])

    item = row_to_item(refreshed) if refreshed is not None else None
    store._log(
        "claim_signal",
        {
            "namespace": cleaned_namespace,
            "consumer": cleaned_consumer,
            "claimed": True,
            "signal_id": candidate["id"],
            "lease_expires_at": lease_expires_at,
        },
    )
    return {
        "claimed": True,
        "signal_id": candidate["id"],
        "namespace": cleaned_namespace,
        "consumer": cleaned_consumer,
        "lease_expires_at": lease_expires_at,
        "item": item,
    }


def ack_signal_entry(
    *,
    store: Any,
    memory_id: str,
    consumer: str | None,
    fetch_row_by_id: Callable[[sqlite3.Connection, str], sqlite3.Row | None],
    row_to_item: Callable[[sqlite3.Row], dict[str, Any]],
) -> dict[str, Any]:
    cleaned_id = memory_id.strip()
    cleaned_consumer = consumer.strip() if consumer else None
    if not cleaned_id:
        raise ValueError("id must not be empty")

    now = datetime.now(UTC)
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = fetch_row_by_id(conn, cleaned_id)
        if row is None:
            conn.rollback()
            return {"id": cleaned_id, "acked": False, "reason": "missing", "item": None}
        if row["kind"] != "signal":
            conn.rollback()
            raise ValueError("only kind=signal entries can be acknowledged")

        snapshot = SignalSnapshot.from_row(row)
        status = effective_signal_status(snapshot, now=now)
        if status == "expired":
            conn.rollback()
            return {"id": cleaned_id, "acked": False, "reason": "expired", "item": row_to_item(row)}
        if status == "acked":
            conn.rollback()
            return {"id": cleaned_id, "acked": False, "reason": "already-acked", "item": row_to_item(row)}
        if cleaned_consumer and snapshot.claimed_by and snapshot.claimed_by != cleaned_consumer and status == "claimed":
            conn.rollback()
            return {"id": cleaned_id, "acked": False, "reason": "claimed-by-other", "item": row_to_item(row)}

        conn.execute(
            """
            UPDATE memories
            SET signal_status = 'acked',
                acknowledged_at = ?,
                lease_expires_at = NULL
            WHERE id = ?
            """,
            (now.isoformat(), cleaned_id),
        )
        conn.commit()
        refreshed = fetch_row_by_id(conn, cleaned_id)

    item = row_to_item(refreshed) if refreshed is not None else None
    store._log("ack_signal", {"id": cleaned_id, "acked": True, "consumer": cleaned_consumer})
    return {"id": cleaned_id, "acked": True, "consumer": cleaned_consumer, "item": item}


def extend_signal_lease_entry(
    *,
    store: Any,
    memory_id: str,
    consumer: str,
    lease_seconds: int,
    fetch_row_by_id: Callable[[sqlite3.Connection, str], sqlite3.Row | None],
    row_to_item: Callable[[sqlite3.Row], dict[str, Any]],
) -> dict[str, Any]:
    cleaned_id = memory_id.strip()
    cleaned_consumer = consumer.strip()
    if not cleaned_id:
        raise ValueError("id must not be empty")
    if not cleaned_consumer:
        raise ValueError("consumer must not be empty")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be greater than 0")

    now = datetime.now(UTC)
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = fetch_row_by_id(conn, cleaned_id)
        if row is None:
            conn.rollback()
            return {"id": cleaned_id, "extended": False, "reason": "missing", "item": None}
        if row["kind"] != "signal":
            conn.rollback()
            raise ValueError("only kind=signal entries can extend a lease")

        snapshot = SignalSnapshot.from_row(row)
        status = effective_signal_status(snapshot, now=now)
        if status == "expired":
            conn.rollback()
            return {"id": cleaned_id, "extended": False, "reason": "expired", "item": row_to_item(row)}
        if status == "acked":
            conn.rollback()
            return {"id": cleaned_id, "extended": False, "reason": "already-acked", "item": row_to_item(row)}
        if _is_expired(snapshot.lease_expires_at, now):
            conn.rollback()
            return {"id": cleaned_id, "extended": False, "reason": "lease-expired", "item": row_to_item(row)}
        if status != "claimed" or not snapshot.claimed_by:
            conn.rollback()
            return {"id": cleaned_id, "extended": False, "reason": "not-claimed", "item": row_to_item(row)}
        if snapshot.claimed_by != cleaned_consumer:
            conn.rollback()
            return {"id": cleaned_id, "extended": False, "reason": "claimed-by-other", "item": row_to_item(row)}

        previous_lease_expires_at = snapshot.lease_expires_at
        lease_expires_at = _resolve_extended_lease_expiry(snapshot=snapshot, now=now, lease_seconds=lease_seconds)
        conn.execute(
            """
            UPDATE memories
            SET lease_expires_at = ?
            WHERE id = ?
            """,
            (lease_expires_at, cleaned_id),
        )
        conn.commit()
        refreshed = fetch_row_by_id(conn, cleaned_id)

    item = row_to_item(refreshed) if refreshed is not None else None
    store._log(
        "extend_signal_lease",
        {
            "id": cleaned_id,
            "consumer": cleaned_consumer,
            "extended": True,
            "previous_lease_expires_at": previous_lease_expires_at,
            "lease_expires_at": lease_expires_at,
        },
    )
    return {
        "id": cleaned_id,
        "extended": True,
        "consumer": cleaned_consumer,
        "previous_lease_expires_at": previous_lease_expires_at,
        "lease_expires_at": lease_expires_at,
        "item": item,
    }


def _is_expired(raw_value: str | None, now: datetime) -> bool:
    parsed = _parse_iso_utc(raw_value)
    return parsed is not None and parsed <= now


def _resolve_extended_lease_expiry(*, snapshot: SignalSnapshot, now: datetime, lease_seconds: int) -> str:
    current_lease = _parse_iso_utc(snapshot.lease_expires_at)
    hard_expiry = _parse_iso_utc(snapshot.expires_at)

    base = current_lease if current_lease and current_lease > now else now
    extended = base + timedelta(seconds=lease_seconds)
    if hard_expiry is not None and extended > hard_expiry:
        extended = hard_expiry
    return extended.isoformat()


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
