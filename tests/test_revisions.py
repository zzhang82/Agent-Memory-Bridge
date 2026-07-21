from __future__ import annotations

from pathlib import Path

import pytest

from agent_mem_bridge import revisions as revisions_module
from agent_mem_bridge import server
from agent_mem_bridge.storage import MemoryStore


def test_duplicate_response_distinguishes_new_metadata_without_mutating_original(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    first = store.store(
        namespace="project:revisions",
        content="record_type: learn\nclaim: Keep duplicate writes auditable.",
        title="Original title",
        tags=["kind:learn", "domain:storage"],
        source_client="codex",
        correlation_id="first-correlation",
    )

    duplicate = store.store(
        namespace="project:revisions",
        content="record_type: learn\nclaim: Keep duplicate writes auditable.",
        title="Improved title",
        tags=["kind:learn", "domain:storage", "topic:dedup"],
        source_client="claude-code",
        correlation_id="second-correlation",
    )

    assert duplicate["id"] == first["id"]
    assert duplicate["write_disposition"] == "duplicate_with_new_metadata"
    assert duplicate["metadata_diff"]["new_tags"] == ["topic:dedup"]
    assert duplicate["metadata_diff"]["different_title"] is True
    assert duplicate["metadata_diff"]["new_provenance"] == {
        "correlation_id": {"existing": "first-correlation", "requested": "second-correlation"},
        "source_client": {"existing": "codex", "requested": "claude-code"},
    }
    item = store.recall(namespace="project:revisions", query="duplicate writes", limit=1)["items"][0]
    assert item["title"] == "Original title"
    assert "topic:dedup" not in item["tags"]


def test_explicit_annotation_updates_metadata_and_keeps_audit_receipt(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = store.store(
        namespace="project:revisions",
        content="record_type: learn\nclaim: Annotation content stays immutable.",
        title="Original title",
        tags=["kind:learn", "domain:storage"],
    )

    result = store.annotate(
        str(created["id"]),
        tags=["topic:dedup"],
        title="Reviewed title",
        provenance={"source_client": "reviewer-client", "correlation_id": "review-1"},
        actor="human-reviewer",
    )

    assert result["changed"] is True
    assert result["added_tags"] == ["topic:dedup"]
    item = result["item"]
    assert item["content"] == "record_type: learn\nclaim: Annotation content stays immutable."
    assert item["title"] == "Reviewed title"
    assert "topic:dedup" in item["tags"]
    assert item["annotations"] == [
        {
            "annotation_id": 1,
            "title_before": "Original title",
            "title_after": "Reviewed title",
            "actor": "human-reviewer",
            "created_at": item["annotations"][0]["created_at"],
            "added_tags": ["topic:dedup"],
            "provenance": {"correlation_id": "review-1", "source_client": "reviewer-client"},
        }
    ]
    recalled = store.recall(
        namespace="project:revisions",
        tags_any=["topic:dedup"],
        limit=1,
    )
    assert recalled["items"][0]["id"] == created["id"]


def test_explicit_revision_creates_successor_and_preserves_predecessor(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    predecessor = store.store(
        namespace="project:revisions",
        content="record_type: learn\nclaim: Old guidance.",
        title="Old guidance",
        tags=["kind:learn", "domain:storage"],
        actor="author-a",
    )

    revision = store.revise(
        str(predecessor["id"]),
        replacement_content="record_type: learn\nclaim: Corrected guidance.",
        title="Corrected guidance",
        actor="author-b",
        reason="Correction after verification",
    )

    successor_id = revision["successor_id"]
    assert successor_id != predecessor["id"]
    old_item = store.recall(namespace="project:revisions", query="Old guidance", limit=1)["items"][0]
    new_item = store.recall(namespace="project:revisions", query="Corrected guidance", limit=1)["items"][0]
    assert old_item["id"] == predecessor["id"]
    assert new_item["id"] == successor_id
    assert new_item["relations"]["supersedes"] == [predecessor["id"]]
    with store._connect() as conn:
        receipt = conn.execute("SELECT * FROM memory_revisions").fetchone()
    assert receipt["predecessor_id"] == predecessor["id"]
    assert receipt["successor_id"] == successor_id
    assert receipt["actor"] == "author-b"
    assert receipt["reason"] == "Correction after verification"


def test_server_exposes_explicit_annotate_and_revise_tools(tmp_path: Path, monkeypatch) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    monkeypatch.setattr(server, "bridge", store)
    created = server.store(
        namespace="project:revisions",
        content="record_type: learn\nclaim: Server revision source.",
        tags=["kind:learn"],
    )

    annotated = server.annotate(
        id=str(created["id"]),
        tags=["topic:server"],
        provenance={"source_client": "test-client"},
        actor="reviewer",
    )
    revised = server.revise(
        id=str(created["id"]),
        replacement_content="record_type: learn\nclaim: Server revision successor.",
        actor="reviewer",
    )

    assert annotated["changed"] is True
    assert revised["predecessor_id"] == created["id"]
    assert revised["successor_id"] != created["id"]


def test_annotation_rejects_reserved_policy_tags(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    created = store.store(namespace="project:revisions", content="record_type: learn\nclaim: Stable authority.")

    with pytest.raises(ValueError, match="reserved policy tags"):
        store.annotate(str(created["id"]), tags=["source:reviewed", "confidence:human-reviewed"])

    item = store.recall(namespace="project:revisions", query="Stable authority", limit=1)["items"][0]
    assert "source:reviewed" not in item["tags"]


def test_revision_rejects_hidden_learning_candidate_authority_bypass(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    candidate = store.store(
        namespace="project:revisions",
        content="record_type: learning-candidate\nclaim: Unreviewed candidate.",
        tags=["kind:learning-candidate", "candidate_status:pending"],
        source_app="amb-learning-layer",
    )
    assert store.recall(namespace="project:revisions", query="Unreviewed candidate", limit=5)["count"] == 0

    with pytest.raises(ValueError, match="governed review workflow"):
        store.revise(
            str(candidate["id"]),
            replacement_content="record_type: learn\nclaim: Illegitimate promotion.",
        )

    assert store.recall(namespace="project:revisions", query="Illegitimate promotion", limit=5)["count"] == 0


@pytest.mark.parametrize(
    ("record_type", "hidden_tag"),
    [
        ("learning-candidate", "kind:learning-candidate"),
        ("learning-review", "kind:learning-review"),
    ],
)
def test_promote_rejects_every_hidden_review_lane_record(
    tmp_path: Path,
    record_type: str,
    hidden_tag: str,
) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    hidden = store.store(
        namespace="project:revisions",
        content=f"record_type: {record_type}\nclaim: Hidden review authority.",
        tags=[hidden_tag, "candidate_status:approved"],
    )

    with pytest.raises(ValueError, match="cannot be promoted directly"):
        store.promote(str(hidden["id"]), "learn")

    assert store.recall(namespace="project:revisions", query="Hidden review authority", limit=5)["count"] == 0


def test_revision_rejects_caller_supplied_reserved_policy_tags(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    source = store.store(
        namespace="project:revisions",
        content="record_type: learn\nclaim: Revision source.",
        tags=["kind:learn", "domain:storage"],
    )

    with pytest.raises(ValueError, match="revise cannot add reserved policy tags"):
        store.revise(
            str(source["id"]),
            replacement_content="record_type: learn\nclaim: Illegitimate reviewed successor.",
            tags=["domain:storage", "source:reviewed", "confidence:human-reviewed", "reviewed:true"],
        )

    assert store.recall(namespace="project:revisions", query="Illegitimate reviewed successor", limit=5)["count"] == 0


def test_revision_does_not_inherit_reserved_policy_tags_from_predecessor(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    source = store.store(
        namespace="project:revisions",
        content="record_type: learn\nclaim: Reviewed predecessor.",
        tags=[
            "kind:learn",
            "domain:storage",
            "source:reviewed",
            "confidence:human-reviewed",
            "reviewed:true",
            "status:approved",
        ],
    )

    revised = store.revise(
        str(source["id"]),
        replacement_content="record_type: learn\nclaim: Unreviewed successor content.",
    )
    successor = store.recall(
        namespace="project:revisions",
        query="Unreviewed successor content",
        limit=1,
    )["items"][0]

    assert successor["id"] == revised["successor_id"]
    assert "kind:learn" in successor["tags"]
    assert "domain:storage" in successor["tags"]
    assert "source:reviewed" not in successor["tags"]
    assert "confidence:human-reviewed" not in successor["tags"]
    assert "reviewed:true" not in successor["tags"]
    assert "status:approved" not in successor["tags"]


def test_revision_successor_and_receipt_roll_back_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    predecessor = store.store(
        namespace="project:revisions",
        content="record_type: learn\nclaim: Original atomic revision.",
    )

    def fail_projection(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("projection failure")

    monkeypatch.setattr(revisions_module, "sync_record_projection", fail_projection)
    with pytest.raises(RuntimeError, match="projection failure"):
        store.revise(
            str(predecessor["id"]),
            replacement_content="record_type: learn\nclaim: Rolled back successor.",
        )

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM memory_revisions").fetchone()[0] == 0
