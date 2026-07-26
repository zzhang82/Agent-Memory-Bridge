from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_mem_bridge.index_health import rebuild_embedding_index
from agent_mem_bridge.retrieval_feedback import (
    FEEDBACK_OUTCOMES,
    decode_recall_receipt,
    encode_recall_receipt,
    recall_receipt_hash,
)
from agent_mem_bridge.schema import rotate_database_epoch
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.telemetry import Telemetry, TelemetryConfig, hash_label

NAMESPACE = "project:v0250-feedback"


def _isolate_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(config_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RECALL_RECEIPT_SECRET_PATH", str(tmp_path / "receipt-secret.json"))
    for name in (
        "AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_CAPABILITY",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_DIM",
        "AGENT_MEMORY_BRIDGE_TELEMETRY_MODE",
        "AGENT_MEMORY_BRIDGE_TELEMETRY_LOG_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def _new_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    telemetry: Telemetry | None = None,
) -> MemoryStore:
    _isolate_runtime(tmp_path, monkeypatch)
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs", telemetry=telemetry)


def _seed_recall_receipt(store: MemoryStore) -> tuple[str, str, str, list[str]]:
    first = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="Alpha protocol exact",
        content="record_type: belief\nclaim: Alpha protocol exact durable answer.",
        tags=["kind:belief", "domain:feedback"],
    )
    store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="Alpha archive note",
        content="Alpha protocol archive background.",
        tags=["domain:feedback"],
    )
    recalled = store.recall(namespace=NAMESPACE, query="alpha protocol exact", kind="memory", limit=5)
    token = str(recalled["recall_receipt"]["token"])
    order = [str(item["id"]) for item in recalled["items"]]
    assert order[0] == first["id"]
    return str(first["id"]), token, "alpha protocol exact", order


def _feedback_count(store: MemoryStore) -> int:
    with store._connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM retrieval_feedback").fetchone()[0])


def _feedback_rows(store: MemoryStore) -> list[dict[str, Any]]:
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM retrieval_feedback
            ORDER BY feedback_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _authority_snapshot(store: MemoryStore) -> dict[str, list[dict[str, Any]]]:
    with store._connect() as conn:
        return {
            "memories": _rows(conn, "SELECT * FROM memories ORDER BY id"),
            "memory_insertions": _rows(conn, "SELECT * FROM memory_insertions ORDER BY sequence"),
            "memory_metadata": _rows(conn, "SELECT * FROM memory_metadata ORDER BY memory_id"),
            "memory_tags": _rows(conn, "SELECT * FROM memory_tags ORDER BY memory_id, tag"),
            "memory_edges": _rows(conn, "SELECT * FROM memory_edges ORDER BY source_id, relation, target_id"),
            "memories_fts": _rows(conn, "SELECT memory_id, title, content FROM memories_fts ORDER BY memory_id"),
            "memory_embeddings": _rows(conn, "SELECT * FROM memory_embeddings ORDER BY memory_id"),
        }


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def test_feedback_exact_retry_is_duplicate_for_stable_caller_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    memory_id, token, _query, _order = _seed_recall_receipt(store)
    request = {
        "namespace": NAMESPACE,
        "recall_receipt": token,
        "memory_id": memory_id,
        "result_rank": 1,
        "outcome": "helpful",
        "source_app": "pytest",
        "source_client": "codex",
        "source_model": "gpt-private",
        "client_session_id": "session-private",
        "client_workspace": "D:\\private\\workspace",
        "client_transport": "stdio",
        "actor": "builder",
    }

    first = store.feedback(**request)
    retry = store.feedback(**request)

    assert first["stored"] is True
    assert first["duplicate"] is False
    assert retry["stored"] is False
    assert retry["duplicate"] is True
    assert retry["feedback_id"] == first["feedback_id"]
    assert first["namespace_hash"] == hash_label(NAMESPACE)
    assert first["memory_id_hash"] == hash_label(memory_id)
    assert first["receipt_bound"] is True
    assert retry["feedback_mode"] == "shadow_only"
    assert retry["ordering"] == "unchanged"
    assert retry["ordering_unchanged"] is True
    forbidden_keys = {
        "namespace",
        "memory_id",
        "reason",
        "receipt_hash",
        "source_app",
        "source_client",
        "source_model",
        "client_session_id",
        "client_workspace",
        "client_transport",
        "actor",
        "created_at",
    }
    assert forbidden_keys.isdisjoint(first)
    encoded_response = json.dumps([first, retry], sort_keys=True)
    for value in (
        token,
        recall_receipt_hash(token),
        "pytest",
        "codex",
        "gpt-private",
        "session-private",
        "D:\\private\\workspace",
        "stdio",
        "builder",
    ):
        assert value not in encoded_response
    assert _feedback_count(store) == 1


def test_feedback_rejects_conflicting_outcome_or_reason_for_same_stable_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    memory_id, token, _query, _order = _seed_recall_receipt(store)
    identity = {
        "namespace": NAMESPACE,
        "recall_receipt": token,
        "memory_id": memory_id,
        "result_rank": 1,
        "source_client": "same-client",
        "client_session_id": "same-session",
    }

    stored = store.feedback(**identity, outcome="misleading", reason="wrong file")
    assert stored["stored"] is True

    with pytest.raises(ValueError, match="conflicting feedback"):
        store.feedback(**identity, outcome="helpful", reason=None)
    with pytest.raises(ValueError, match="conflicting feedback"):
        store.feedback(**identity, outcome="misleading", reason="wrong namespace")

    rows = _feedback_rows(store)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "misleading"
    assert rows[0]["reason"] == "wrong file"


def test_feedback_accepts_all_declared_outcomes_and_keeps_not_used_shadow_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    memory_id, token, _query, _order = _seed_recall_receipt(store)
    reasons = {
        "helpful": None,
        "misleading": "wrong conclusion",
        "outdated": "policy expired",
        "not_applicable": None,
        "not_used": None,
    }

    for index, outcome in enumerate(sorted(FEEDBACK_OUTCOMES), start=1):
        result = store.feedback(
            namespace=NAMESPACE,
            recall_receipt=token,
            memory_id=memory_id,
            result_rank=1,
            outcome=outcome,
            reason=reasons[outcome],
            source_client=f"client-{index}",
            client_session_id=f"session-{index}",
        )
        assert result["stored"] is True
        assert result["outcome"] == outcome
        assert result["feedback_mode"] == "shadow_only"
        assert result["ordering"] == "unchanged"
        assert result["diagnostics"]["returned_ordering_changed"] is False
        if outcome == "not_used":
            assert result["ordering_unchanged"] is True

    assert {row["outcome"] for row in _feedback_rows(store)} == FEEDBACK_OUTCOMES


@pytest.mark.parametrize("outcome", ["misleading", "outdated"])
def test_feedback_requires_reason_for_misleading_and_outdated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    memory_id, token, _query, _order = _seed_recall_receipt(store)

    with pytest.raises(ValueError, match="reason is required"):
        store.feedback(
            namespace=NAMESPACE,
            recall_receipt=token,
            memory_id=memory_id,
            result_rank=1,
            outcome=outcome,
            reason="  ",
        )

    assert _feedback_count(store) == 0


def test_feedback_enforces_reason_length_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    memory_id, token, _query, _order = _seed_recall_receipt(store)

    accepted = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=memory_id,
        result_rank=1,
        outcome="outdated",
        reason="x" * 280,
        source_client="client-a",
        client_session_id="session-a",
    )
    assert accepted["stored"] is True

    with pytest.raises(ValueError, match="280 characters or fewer"):
        store.feedback(
            namespace=NAMESPACE,
            recall_receipt=token,
            memory_id=memory_id,
            result_rank=1,
            outcome="outdated",
            reason="x" * 281,
            source_client="client-b",
            client_session_id="session-b",
        )

    assert _feedback_count(store) == 1


@pytest.mark.parametrize("outcome", ["", "bad", "raw_score", " helpfulness "])
def test_feedback_rejects_unknown_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    memory_id, token, _query, _order = _seed_recall_receipt(store)

    with pytest.raises(ValueError, match="outcome must be one of"):
        store.feedback(
            namespace=NAMESPACE,
            recall_receipt=token,
            memory_id=memory_id,
            result_rank=1,
            outcome=outcome,
        )

    assert _feedback_count(store) == 0


@pytest.mark.parametrize(
    ("case_name", "expected_error"),
    [
        ("tampered_signature", "signature mismatch"),
        ("expired", "expired"),
        ("bridge_instance", "bridge instance mismatch"),
        ("database_epoch", "database epoch mismatch"),
        ("namespace", "namespace mismatch"),
        ("memory_id", "memory id and rank mismatch"),
        ("rank", "memory id and rank mismatch"),
    ],
)
def test_feedback_rejects_invalid_receipt_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    expected_error: str,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    memory_id, token, _query, _order = _seed_recall_receipt(store)
    namespace = NAMESPACE
    result_rank = 1
    receipt = token
    payload = decode_recall_receipt(token, secret=store.recall_receipt_secret)

    if case_name == "tampered_signature":
        receipt = f"{token[:-1]}{'A' if token[-1] != 'A' else 'B'}"
    elif case_name == "expired":
        now = datetime.now(UTC)
        receipt = encode_recall_receipt(
            {
                **payload,
                "issued_at": (now - timedelta(minutes=2)).isoformat(),
                "expires_at": (now - timedelta(minutes=1)).isoformat(),
            },
            secret=store.recall_receipt_secret,
        )
    elif case_name == "bridge_instance":
        receipt = encode_recall_receipt(
            {**payload, "bridge_instance_id": "not-this-bridge"},
            secret=store.recall_receipt_secret,
        )
    elif case_name == "database_epoch":
        with store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rotate_database_epoch(conn)
            conn.commit()
    elif case_name == "namespace":
        namespace = "project:other"
    elif case_name == "memory_id":
        memory_id = "not-the-memory"
    elif case_name == "rank":
        result_rank = 2

    with pytest.raises(ValueError, match=expected_error):
        store.feedback(
            namespace=namespace,
            recall_receipt=receipt,
            memory_id=memory_id,
            result_rank=result_rank,
            outcome="helpful",
        )

    assert _feedback_count(store) == 0


def test_feedback_does_not_mutate_memories_indexes_belief_records_or_default_ranking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    memory_id, token, query, before_order = _seed_recall_receipt(store)
    with store._connect() as conn:
        rebuild_embedding_index(conn)
        conn.commit()
    before_snapshot = _authority_snapshot(store)

    for index, outcome in enumerate(("helpful", "not_used"), start=1):
        result = store.feedback(
            namespace=NAMESPACE,
            recall_receipt=token,
            memory_id=memory_id,
            result_rank=1,
            outcome=outcome,
            source_client=f"ranking-client-{index}",
            client_session_id=f"ranking-session-{index}",
        )
        assert result["feedback_mode"] == "shadow_only"
        assert result["ordering"] == "unchanged"

    after_snapshot = _authority_snapshot(store)
    after_order = [
        str(item["id"]) for item in store.recall(namespace=NAMESPACE, query=query, kind="memory", limit=5)["items"]
    ]

    assert after_snapshot == before_snapshot
    assert after_order == before_order
    assert _feedback_count(store) == 2


def test_feedback_logs_and_telemetry_are_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = Telemetry(TelemetryConfig(mode="jsonl", log_dir=tmp_path / "telemetry"))
    store = _new_store(tmp_path, monkeypatch, telemetry=telemetry)
    raw_query = "Private feedback target"
    raw_content = "Private feedback content D:\\hidden\\customer"
    raw_reason = "wrong because D:\\private\\workspace has stale private fact"
    raw_source_client = "client-secret-name"
    raw_source_model = "model-secret-name"
    raw_session = "session-secret-name"
    raw_workspace = "D:\\private\\workspace"
    created = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="Private feedback target",
        content=raw_content,
    )
    recalled = store.recall(namespace=NAMESPACE, query=raw_query, kind="memory", limit=5)
    token = str(recalled["recall_receipt"]["token"])

    store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=str(created["id"]),
        result_rank=1,
        outcome="outdated",
        reason=raw_reason,
        source_app="pytest",
        source_client=raw_source_client,
        source_model=raw_source_model,
        client_session_id=raw_session,
        client_workspace=raw_workspace,
        client_transport="D:\\not-a-transport",
    )

    feedback_log = (tmp_path / "logs" / "feedback.log").read_text(encoding="utf-8")
    recall_log = (tmp_path / "logs" / "recall.log").read_text(encoding="utf-8")
    telemetry_log = (tmp_path / "telemetry" / "spans.jsonl").read_text(encoding="utf-8")
    combined_logs = feedback_log + recall_log + telemetry_log
    forbidden_values = [
        raw_query,
        raw_content,
        raw_reason,
        token,
        raw_source_client,
        raw_source_model,
        raw_session,
        raw_workspace,
    ]
    for value in forbidden_values:
        assert value not in combined_logs

    feedback_entry = json.loads(feedback_log.strip().splitlines()[-1])
    assert "reason" not in feedback_entry
    assert "recall_receipt" not in feedback_entry
    assert "client_workspace" not in feedback_entry
    assert feedback_entry["has_reason"] is True
    assert feedback_entry["source_client_hash"] == hash_label(raw_source_client)
    assert feedback_entry["client_session_hash"] == hash_label(raw_session)
    assert feedback_entry["client_transport"] == "other"

    spans = [json.loads(line) for line in telemetry_log.splitlines()]
    feedback_span = next(span for span in spans if span["name"] == "amb.feedback.record")
    attributes = feedback_span["attributes"]
    assert attributes["outcome"] == "outdated"
    assert attributes["has_reason"] is True
    assert attributes["source_client_hash"] == hash_label(raw_source_client)
    assert attributes["client_session_hash"] == hash_label(raw_session)
    assert attributes["client_transport"] == "other"
