from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import agent_mem_bridge.retrieval_feedback as feedback_module
from agent_mem_bridge.retrieval_feedback import decode_recall_receipt, encode_recall_receipt
from agent_mem_bridge.storage import MemoryStore

NAMESPACE = "project:v0252-effective-feedback"


def _new_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(config_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(
        "AGENT_MEMORY_BRIDGE_RECALL_RECEIPT_SECRET_PATH",
        str(tmp_path / "receipt-secret.json"),
    )
    for name in (
        "AGENT_MEMORY_BRIDGE_RETRIEVAL_MODE",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_CAPABILITY",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL",
        "AGENT_MEMORY_BRIDGE_EMBEDDING_DIM",
    ):
        monkeypatch.delenv(name, raising=False)
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


def _seed_receipt(store: MemoryStore, *, count: int = 2) -> tuple[str, list[dict[str, Any]]]:
    for index in range(count):
        store.store(
            namespace=NAMESPACE,
            kind="memory",
            title=f"effective feedback target {index}",
            content=f"effective feedback durable body {index}",
        )
    recalled = store.recall(
        namespace=NAMESPACE,
        query="effective feedback target",
        kind="memory",
        limit=count,
    )
    assert len(recalled["items"]) == count
    return str(recalled["recall_receipt"]["token"]), recalled["items"]


def _feedback_rows(store: MemoryStore) -> list[dict[str, Any]]:
    with store._connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM retrieval_feedback
                ORDER BY feedback_id
                """
            ).fetchall()
        ]


def _effective_rows(store: MemoryStore) -> list[dict[str, Any]]:
    with store._connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM retrieval_feedback_effective_votes
                ORDER BY feedback_id
                """
            ).fetchall()
        ]


def test_caller_provenance_cannot_inflate_or_change_a_subject_vote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    token, items = _seed_receipt(store, count=1)
    request = {
        "namespace": NAMESPACE,
        "recall_receipt": token,
        "memory_id": str(items[0]["id"]),
        "result_rank": 1,
        "outcome": "helpful",
    }

    def submit(index: int) -> dict[str, Any]:
        return store.feedback(
            **request,
            source_app=f"app-{index}",
            source_client=f"client-{index}",
            source_model=f"model-{index}",
            client_session_id=f"session-{index}",
            client_workspace=f"workspace-{index}",
            client_transport="stdio" if index % 2 else "http",
            actor=f"actor-{index}",
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(submit, range(12)))

    assert sum(result["stored"] is True for result in results) == 1
    assert len({result["feedback_id"] for result in results}) == 1
    assert all(result["provenance"] == "caller_declared_not_authenticated" for result in results)
    assert len(_feedback_rows(store)) == 1
    assert len(_effective_rows(store)) == 1

    with pytest.raises(ValueError, match="submit a correction"):
        store.feedback(
            **{**request, "outcome": "not_used"},
            source_client="new-client",
            client_session_id="new-session",
        )


def test_correction_chain_retraction_and_subject_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    token, items = _seed_receipt(store)
    original_order = [str(item["id"]) for item in items]
    first = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=str(items[0]["id"]),
        result_rank=1,
        outcome="helpful",
    )
    other_subject = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=str(items[1]["id"]),
        result_rank=2,
        outcome="not_used",
    )

    with pytest.raises(ValueError, match="current feedback head"):
        store.feedback(
            namespace=NAMESPACE,
            recall_receipt=token,
            memory_id=str(items[0]["id"]),
            result_rank=1,
            feedback_type="correction",
            supersedes_feedback_id=other_subject["feedback_id"],
            outcome="outdated",
            reason="wrong subject",
        )

    correction = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=str(items[0]["id"]),
        result_rank=1,
        feedback_type="correction",
        supersedes_feedback_id=first["feedback_id"],
        outcome="outdated",
        reason="superseded fact",
    )
    retry = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=str(items[0]["id"]),
        result_rank=1,
        feedback_type="correction",
        supersedes_feedback_id=first["feedback_id"],
        outcome="outdated",
        reason="superseded fact",
        source_client="retry-client",
        client_session_id="retry-session",
    )
    assert retry["stored"] is False
    assert retry["feedback_id"] == correction["feedback_id"]
    stale_root_retry = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=str(items[0]["id"]),
        result_rank=1,
        outcome="helpful",
    )
    assert stale_root_retry["stored"] is False
    assert stale_root_retry["feedback_id"] == first["feedback_id"]
    assert stale_root_retry["effective_vote"] is False
    assert [row["feedback_id"] for row in _effective_rows(store)] == [
        other_subject["feedback_id"],
        correction["feedback_id"],
    ]

    retraction = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=str(items[0]["id"]),
        result_rank=1,
        feedback_type="retraction",
        supersedes_feedback_id=correction["feedback_id"],
        reason="withdrawn",
    )
    assert retraction["effective_vote"] is False
    stale_correction_retry = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=str(items[0]["id"]),
        result_rank=1,
        feedback_type="correction",
        supersedes_feedback_id=first["feedback_id"],
        outcome="outdated",
        reason="superseded fact",
    )
    assert stale_correction_retry["stored"] is False
    assert stale_correction_retry["feedback_id"] == correction["feedback_id"]
    assert stale_correction_retry["effective_vote"] is False
    assert [row["feedback_id"] for row in _effective_rows(store)] == [other_subject["feedback_id"]]

    restored = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=str(items[0]["id"]),
        result_rank=1,
        feedback_type="correction",
        supersedes_feedback_id=retraction["feedback_id"],
        outcome="helpful",
    )
    assert [row["feedback_id"] for row in _effective_rows(store)] == [
        other_subject["feedback_id"],
        restored["feedback_id"],
    ]
    recalled_again = store.recall(
        namespace=NAMESPACE,
        query="effective feedback target",
        kind="memory",
        limit=2,
    )
    assert [str(item["id"]) for item in recalled_again["items"]] == original_order


def test_schema_blocks_root_branches_and_preserves_append_only_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    token, items = _seed_receipt(store, count=1)
    root = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=str(items[0]["id"]),
        result_rank=1,
        outcome="helpful",
    )
    correction = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=token,
        memory_id=str(items[0]["id"]),
        result_rank=1,
        feedback_type="correction",
        supersedes_feedback_id=root["feedback_id"],
        outcome="not_used",
    )

    with store._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE retrieval_feedback SET outcome = 'helpful' WHERE feedback_id = ?", (root["feedback_id"],)
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM retrieval_feedback WHERE feedback_id = ?", (correction["feedback_id"],))
        conn.rollback()

        clone_sql = """
            INSERT INTO retrieval_feedback (
                idempotency_key, receipt_hash, feedback_identity_digest,
                namespace, memory_id, result_rank, outcome, reason, retrieval_mode,
                database_epoch, bridge_instance_id, receipt_issued_at, receipt_expires_at, feedback_json,
                source_app, source_client, source_model, client_session_id, client_workspace, client_transport,
                actor, created_at, feedback_type, supersedes_feedback_id
            )
            SELECT
                ?, receipt_hash, feedback_identity_digest,
                namespace, memory_id, result_rank, outcome, reason, retrieval_mode,
                database_epoch, bridge_instance_id, receipt_issued_at, receipt_expires_at, feedback_json,
                source_app, source_client, source_model, client_session_id, client_workspace, client_transport,
                actor, created_at, ?, ?
            FROM retrieval_feedback
            WHERE feedback_id = ?
        """
        with pytest.raises(sqlite3.IntegrityError, match="root vote"):
            conn.execute(clone_sql, ("a" * 64, "vote", None, root["feedback_id"]))
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="current head|UNIQUE"):
            conn.execute(clone_sql, ("b" * 64, "correction", root["feedback_id"], correction["feedback_id"]))
        conn.rollback()

    assert [row["feedback_id"] for row in _feedback_rows(store)] == [
        root["feedback_id"],
        correction["feedback_id"],
    ]
    assert [row["feedback_id"] for row in _effective_rows(store)] == [correction["feedback_id"]]


def test_feedback_persists_complete_signed_exposure_after_receipt_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _new_store(tmp_path, monkeypatch)
    token, items = _seed_receipt(store, count=3)
    payload = decode_recall_receipt(token, secret=store.recall_receipt_secret)
    now = datetime.now(UTC)
    short_payload = {
        **payload,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=1)).isoformat(),
    }
    short_token = encode_recall_receipt(short_payload, secret=store.recall_receipt_secret)

    stored = store.feedback(
        namespace=NAMESPACE,
        recall_receipt=short_token,
        memory_id=str(items[1]["id"]),
        result_rank=2,
        outcome="helpful",
    )
    feedback_row = _feedback_rows(store)[0]
    evidence = json.loads(feedback_row["feedback_json"])
    expected_exposure = short_payload["exposure_set"][1]
    assert feedback_row["receipt_hash"] == feedback_module.recall_receipt_hash(short_token)
    assert feedback_row["feedback_identity_digest"] == feedback_module.canonical_feedback_identity_digest(short_payload)
    assert feedback_row["receipt_hash"] != feedback_row["feedback_identity_digest"]
    assert evidence["exact_content_hash"] == expected_exposure["exact_content_hash"]
    assert evidence["content_version"] == expected_exposure["content_version"]
    assert evidence["retrieval_contract_digest"] == short_payload["retrieval_contract_digest"]
    assert evidence["exposure_set"] == short_payload["exposure_set"]
    assert evidence["provenance"] == "caller_declared_not_authenticated"

    class ExpiredDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            expired = now + timedelta(seconds=2)
            return expired if tz is None else expired.astimezone(tz)

    monkeypatch.setattr(feedback_module, "datetime", ExpiredDateTime)
    with pytest.raises(ValueError, match="expired"):
        feedback_module.validate_recall_receipt(
            store,
            recall_receipt=short_token,
            namespace=NAMESPACE,
            memory_id=str(items[1]["id"]),
            result_rank=2,
        )
    assert json.loads(_feedback_rows(store)[0]["feedback_json"]) == evidence
    assert stored["feedback_mode"] == "shadow_only"
