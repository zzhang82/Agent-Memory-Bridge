from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from agent_mem_bridge.lineage import parse_lineage
from agent_mem_bridge.poll_cursor import decode_poll_cursor
from agent_mem_bridge.promotion import parse_structured_record
from agent_mem_bridge.relation_metadata import parse_content_fields, parse_relation_metadata
from agent_mem_bridge.storage import MemoryStore


def _claim_one_signal(
    db_path: str,
    log_dir: str,
    signal_id: str,
    consumer: str,
    start_event: Any,
    results: Any,
) -> None:
    try:
        store = MemoryStore(Path(db_path), log_dir=Path(log_dir))
        if not start_event.wait(timeout=15):
            results.put((consumer, False, "start-timeout"))
            return
        outcome = store.claim_signal(
            namespace="project:multiprocess-claim",
            consumer=consumer,
            lease_seconds=60,
            signal_id=signal_id,
        )
        results.put((consumer, bool(outcome["claimed"]), outcome.get("reason")))
    except Exception as exc:
        results.put((consumer, False, f"{type(exc).__name__}: {exc}"))


def test_signal_polling_pages_in_insertion_order_without_gaps_or_repeats(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    cursor = store.store(namespace="project:polling", content="cursor", kind="signal")
    expected = [
        store.store(namespace="project:polling", content=f"signal-{index}", kind="signal")["id"] for index in range(257)
    ]

    seen: list[str] = []
    next_since = str(cursor["id"])
    while True:
        page = store.recall(
            namespace="project:polling",
            kind="signal",
            since=next_since,
            limit=17,
        )
        if not page["items"]:
            break
        page_ids = [str(item["id"]) for item in page["items"]]
        seen.extend(page_ids)
        next_since = str(page["next_since"])

    assert seen == expected
    assert len(seen) == len(set(seen))
    decoded = decode_poll_cursor(next_since)
    assert decoded is not None
    assert decoded.namespace == "project:polling"


def test_signal_polling_rejects_invalid_and_cross_namespace_cursors(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    other = store.store(namespace="project:other", content="other cursor", kind="signal")
    deleted = store.store(namespace="project:polling", content="deleted cursor", kind="signal")
    assert store.forget(str(deleted["id"]))["deleted"] is True

    with pytest.raises(ValueError, match="invalid since cursor"):
        store.recall(namespace="project:polling", kind="signal", since="missing-id")
    with pytest.raises(ValueError, match="invalid since cursor"):
        store.recall(namespace="project:polling", kind="signal", since=str(deleted["id"]))
    with pytest.raises(ValueError, match="namespace mismatch"):
        store.recall(namespace="project:polling", kind="signal", since=str(other["id"]))


def test_since_is_reserved_for_empty_query_signal_polling(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    cursor = store.store(namespace="project:polling", content="cursor", kind="signal")

    with pytest.raises(ValueError, match="empty query"):
        store.recall(
            namespace="project:polling",
            query="cursor",
            kind="signal",
            since=str(cursor["id"]),
        )
    with pytest.raises(ValueError, match="kind='signal'"):
        store.recall(namespace="project:polling", kind="memory", since=str(cursor["id"]))


def test_next_since_is_only_exposed_for_empty_query_signal_recall(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    memory = store.store(namespace="project:cursors", content="durable answer", kind="memory")
    first_signal = store.store(namespace="project:cursors", content="first signal", kind="signal")
    second_signal = store.store(namespace="project:cursors", content="second signal", kind="signal")

    queried = store.recall(namespace="project:cursors", query="durable answer", kind="memory")
    filtered = store.recall(namespace="project:cursors", kind="memory")
    signals = store.recall(namespace="project:cursors", kind="signal", limit=2)

    assert queried["items"][0]["id"] == memory["id"]
    assert queried["next_since"] is None
    assert filtered["next_since"] is None
    assert [item["id"] for item in signals["items"]] == [second_signal["id"], first_signal["id"]]
    decoded = decode_poll_cursor(signals["next_since"])
    assert decoded is not None
    assert decoded.namespace == "project:cursors"


def test_opaque_poll_cursor_survives_deletion_of_page_boundary_signal(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    initial = store.store(namespace="project:cursors", content="initial", kind="signal")
    first = store.store(namespace="project:cursors", content="first", kind="signal")
    boundary = store.store(namespace="project:cursors", content="boundary", kind="signal")

    page = store.recall(
        namespace="project:cursors",
        kind="signal",
        since=initial["id"],
        limit=2,
    )
    cursor = str(page["next_since"])
    assert [item["id"] for item in page["items"]] == [first["id"], boundary["id"]]
    store.forget(str(boundary["id"]))
    newest = store.store(namespace="project:cursors", content="newest", kind="signal")

    resumed = store.recall(
        namespace="project:cursors",
        kind="signal",
        since=cursor,
        limit=10,
    )

    assert [item["id"] for item in resumed["items"]] == [newest["id"]]


def test_claimed_signal_ack_requires_current_owner_but_pending_ack_remains_ownerless(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    claimed_signal = store.store(namespace="project:ack", content="claimed", kind="signal")
    pending_signal = store.store(namespace="project:ack", content="pending", kind="signal")
    store.claim_signal(
        namespace="project:ack",
        consumer="worker-a",
        lease_seconds=60,
        signal_id=str(claimed_signal["id"]),
    )

    ownerless = store.ack_signal(str(claimed_signal["id"]))
    blank_owner = store.ack_signal(str(claimed_signal["id"]), consumer="   ")
    non_owner = store.ack_signal(str(claimed_signal["id"]), consumer="worker-b")
    owner = store.ack_signal(str(claimed_signal["id"]), consumer="worker-a")
    pending = store.ack_signal(str(pending_signal["id"]))

    assert ownerless["acked"] is False
    assert ownerless["reason"] == "consumer-required"
    assert blank_owner["acked"] is False
    assert blank_owner["reason"] == "consumer-required"
    assert non_owner["acked"] is False
    assert non_owner["reason"] == "claimed-by-other"
    assert owner["acked"] is True
    assert pending["acked"] is True


def test_claimed_signal_without_owner_is_rejected_as_malformed(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    created = store.store(namespace="project:ack", content="malformed claim", kind="signal")
    with store._connect() as conn:
        conn.execute("DROP TRIGGER validate_signal_state_update")
        conn.execute(
            """
            UPDATE memories
            SET signal_status = 'claimed',
                claimed_by = NULL,
                lease_expires_at = '2099-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (created["id"],),
        )
        conn.commit()

    acked = store.ack_signal(str(created["id"]), consumer="worker-a")

    assert acked["acked"] is False
    assert acked["reason"] == "claim-owner-missing"


def test_structured_parsers_share_case_hyphen_and_duplicate_rules() -> None:
    content = (
        "Claim: first claim\n"
        "claim: ignored duplicate\n"
        "SUPPORTS: support-a | support-b\n"
        "supports: support-b | support-c\n"
        "Derived-From-Candidate-ID: candidate-a\n"
        "derived_from_candidate_id: ignored-candidate\n"
    )

    promotion_fields = parse_structured_record(content)
    relation_fields = parse_content_fields(content)
    relations = parse_relation_metadata(content)
    lineage = parse_lineage(content)

    assert promotion_fields["claim"] == "first claim"
    assert relation_fields["claim"] == "first claim"
    assert promotion_fields["supports"] == "support-a | support-b | support-c"
    assert relation_fields["supports"] == "support-a | support-b | support-c"
    assert relations["relations"]["supports"] == ["support-a", "support-b", "support-c"]
    assert lineage.supports == ("support-a", "support-b", "support-c")
    assert lineage.derived_from_candidate_id == "candidate-a"


def test_operational_log_failure_does_not_reverse_or_duplicate_signal_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")

    def fail_log_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        del self, args, kwargs
        raise OSError("sensitive filesystem detail")

    monkeypatch.setattr(Path, "open", fail_log_open)
    stored = store.store(namespace="project:logging", content="one signal", kind="signal")
    with store._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE namespace = ? AND kind = 'signal'",
            ("project:logging",),
        ).fetchone()[0]

    captured = capsys.readouterr()
    assert stored["stored"] is True
    assert count == 1
    assert captured.out == ""
    assert "operational log write failed" in captured.err
    assert "sensitive filesystem detail" not in captured.err


def test_eight_processes_competing_for_one_signal_have_one_winner(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:multiprocess-claim",
        content="one worker only",
        kind="signal",
    )
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_claim_one_signal,
            args=(
                str(store.db_path),
                str(store.log_dir),
                str(created["id"]),
                f"worker-{index}",
                start_event,
                results,
            ),
        )
        for index in range(8)
    ]

    for process in processes:
        process.start()
    start_event.set()
    outcomes = [results.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)

    assert all(process.exitcode == 0 for process in processes)
    assert all(reason is None or reason in {"claimed-by-other", "no-eligible-signal"} for _, _, reason in outcomes)
    assert sum(claimed for _, claimed, _ in outcomes) == 1
    winner = next(consumer for consumer, claimed, _ in outcomes if claimed)
    with store._connect() as conn:
        stored_owner = conn.execute(
            "SELECT claimed_by FROM memories WHERE id = ?",
            (created["id"],),
        ).fetchone()["claimed_by"]
    assert stored_owner == winner
    results.close()
    results.join_thread()
