from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .storage import MemoryStore


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_PATH = ROOT / "benchmark" / "latest-signal-contention-report.json"


def run_signal_contention_benchmark() -> dict[str, Any]:
    runtime_dir = Path(tempfile.mkdtemp(prefix="agent-memory-bridge-contention-"))
    try:
        cases = [
            run_unique_claims_case(runtime_dir / "unique-claims.db"),
            run_claim_not_renew_case(runtime_dir / "claim-not-renew.db"),
            run_stale_reclaim_case(runtime_dir / "stale-reclaim.db"),
            run_pending_under_pressure_case(runtime_dir / "pending-pressure.db"),
            run_initial_hard_expiry_case(runtime_dir / "hard-expiry.db"),
        ]
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)

    passed_count = sum(1 for case in cases if case["passed"])
    duplicate_active_claim_count = sum(int(case["metrics"].get("duplicate_active_claim_count", 0)) for case in cases)
    return {
        "summary": {
            "case_count": len(cases),
            "case_pass_rate": round(passed_count / len(cases), 3) if cases else 0.0,
            "unique_active_claim_rate": metric_rate(cases, "unique_active_claims"),
            "duplicate_active_claim_count": duplicate_active_claim_count,
            "active_reclaim_block_rate": metric_rate(cases, "active_reclaim_blocked"),
            "stale_ack_blocked_rate": metric_rate(cases, "stale_ack_blocked"),
            "stale_reclaim_success_rate": metric_rate(cases, "stale_reclaim_succeeded"),
            "pending_under_pressure_claim_rate": metric_rate(cases, "pending_under_pressure_claimed"),
            "initial_hard_expiry_cap_rate": metric_rate(cases, "initial_hard_expiry_capped"),
        },
        "metadata": {
            "comparison": "serialized_multi_consumer_signal_contention_contract",
            "notes": (
                "This slice checks repeatable signal ownership and reclaim semantics under contention. "
                "It is not a scheduler, throughput, exactly-once, or distributed-lock benchmark."
            ),
        },
        "cases": cases,
    }


def run_unique_claims_case(db_path: Path) -> dict[str, Any]:
    store = MemoryStore(db_path, log_dir=db_path.parent / "logs-unique")
    for index in range(6):
        store.store(
            namespace="bench:contention",
            content=f"Parallel review item {index}.",
            kind="signal",
            tags=["handoff:contention"],
        )

    claimed_ids: list[str] = []
    for index in range(6):
        claimed = store.claim_signal(
            namespace="bench:contention",
            consumer=f"worker-{index}",
            lease_seconds=60,
            tags_any=["handoff:contention"],
        )
        if claimed["claimed"]:
            claimed_ids.append(claimed["signal_id"])
    exhausted = store.claim_signal(
        namespace="bench:contention",
        consumer="worker-extra",
        lease_seconds=60,
        tags_any=["handoff:contention"],
    )
    unique_count = len(set(claimed_ids))
    metrics = {
        "unique_active_claims": unique_count == 6,
        "duplicate_active_claim_count": len(claimed_ids) - unique_count,
        "exhausted_reason": exhausted.get("reason"),
    }
    return {
        "id": "unique-active-claims",
        "passed": metrics["unique_active_claims"] and exhausted["claimed"] is False and exhausted.get("reason") == "no-eligible-signal",
        "metrics": metrics,
    }


def run_claim_not_renew_case(db_path: Path) -> dict[str, Any]:
    store = MemoryStore(db_path, log_dir=db_path.parent / "logs-renew")
    created = store.store(
        namespace="bench:contention",
        content="Active same-owner claim should require extend.",
        kind="signal",
        tags=["handoff:contention"],
    )
    first = store.claim_signal(
        namespace="bench:contention",
        consumer="worker-a",
        lease_seconds=60,
        signal_id=created["id"],
    )
    second = store.claim_signal(
        namespace="bench:contention",
        consumer="worker-a",
        lease_seconds=60,
        signal_id=created["id"],
    )
    extended = store.extend_signal_lease(created["id"], consumer="worker-a", lease_seconds=60)
    metrics = {
        "active_reclaim_blocked": first["claimed"] is True
        and second["claimed"] is False
        and second.get("reason") == "already-claimed",
        "extend_still_renews": extended["extended"] is True,
        "blocked_reason": second.get("reason"),
    }
    return {
        "id": "claim-is-not-renew",
        "passed": metrics["active_reclaim_blocked"] and metrics["extend_still_renews"],
        "metrics": metrics,
    }


def run_stale_reclaim_case(db_path: Path) -> dict[str, Any]:
    store = MemoryStore(db_path, log_dir=db_path.parent / "logs-stale")
    created = store.store(
        namespace="bench:contention",
        content="Stale owner must not ack after losing the lease.",
        kind="signal",
        tags=["handoff:contention"],
    )
    store.claim_signal(
        namespace="bench:contention",
        consumer="worker-a",
        lease_seconds=60,
        signal_id=created["id"],
    )
    force_expire_lease(store, created["id"])
    stale_ack = store.ack_signal(created["id"], consumer="worker-a")
    reclaimed = store.claim_signal(
        namespace="bench:contention",
        consumer="worker-b",
        lease_seconds=60,
        signal_id=created["id"],
    )
    acked = store.ack_signal(created["id"], consumer="worker-b")
    metrics = {
        "stale_ack_blocked": stale_ack["acked"] is False and stale_ack.get("reason") == "lease-expired",
        "stale_reclaim_succeeded": reclaimed["claimed"] is True and acked["acked"] is True,
        "stale_ack_reason": stale_ack.get("reason"),
    }
    return {
        "id": "stale-reclaim-before-ack",
        "passed": metrics["stale_ack_blocked"] and metrics["stale_reclaim_succeeded"],
        "metrics": metrics,
    }


def run_pending_under_pressure_case(db_path: Path) -> dict[str, Any]:
    store = MemoryStore(db_path, log_dir=db_path.parent / "logs-pressure")
    for index in range(55):
        active = store.store(
            namespace="bench:contention",
            content=f"Active claimed handoff {index}.",
            kind="signal",
            tags=["handoff:contention"],
        )
        store.claim_signal(
            namespace="bench:contention",
            consumer=f"other-worker-{index}",
            lease_seconds=600,
            signal_id=active["id"],
        )
    pending = store.store(
        namespace="bench:contention",
        content="Pending handoff should not be starved.",
        kind="signal",
        tags=["handoff:contention"],
    )
    claimed = store.claim_signal(
        namespace="bench:contention",
        consumer="worker-ready",
        lease_seconds=60,
        tags_any=["handoff:contention"],
    )
    metrics = {
        "pending_under_pressure_claimed": claimed["claimed"] is True and claimed["signal_id"] == pending["id"],
        "claimed_signal_id": claimed.get("signal_id"),
        "expected_signal_id": pending["id"],
    }
    return {
        "id": "pending-not-starved-by-active-claims",
        "passed": metrics["pending_under_pressure_claimed"],
        "metrics": metrics,
    }


def run_initial_hard_expiry_case(db_path: Path) -> dict[str, Any]:
    store = MemoryStore(db_path, log_dir=db_path.parent / "logs-hard-expiry")
    hard_expiry = datetime.now(UTC) + timedelta(seconds=30)
    created = store.store(
        namespace="bench:contention",
        content="Initial claim should be capped by signal hard expiry.",
        kind="signal",
        tags=["handoff:contention"],
        expires_at=hard_expiry.isoformat(),
    )
    claimed = store.claim_signal(
        namespace="bench:contention",
        consumer="worker-a",
        lease_seconds=600,
        signal_id=created["id"],
    )
    metrics = {
        "initial_hard_expiry_capped": claimed["claimed"] is True
        and datetime.fromisoformat(claimed["lease_expires_at"]) <= hard_expiry,
        "expires_at": created["expires_at"],
        "lease_expires_at": claimed.get("lease_expires_at"),
    }
    return {
        "id": "initial-claim-hard-expiry-cap",
        "passed": metrics["initial_hard_expiry_capped"],
        "metrics": metrics,
    }


def metric_rate(cases: list[dict[str, Any]], metric_name: str) -> float:
    values = [case["metrics"].get(metric_name) for case in cases if metric_name in case["metrics"]]
    if not values:
        return 0.0
    return round(sum(1 for value in values if value is True) / len(values), 3)


def force_expire_lease(store: MemoryStore, signal_id: str) -> None:
    with store._connect() as conn:
        conn.execute(
            "UPDATE memories SET lease_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", signal_id),
        )
        conn.commit()
