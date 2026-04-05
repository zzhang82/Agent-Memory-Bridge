from pathlib import Path

from agent_mem_bridge.consolidation import ConsolidationConfig, ConsolidationEngine
from agent_mem_bridge.storage import MemoryStore


def test_consolidation_creates_domain_note_from_recent_learns_and_gotchas(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="cole-core",
        kind="memory",
        title="[[Cole Learn]] Safe FTS fallback",
        content=(
            "record_type: learn\n"
            "claim: Punctuation-heavy queries can break naive FTS recall paths.\n"
            "scope: global\n"
            "confidence: observed"
        ),
        tags=["kind:learn", "domain:retrieval", "topic:fts", "project:mem-store"],
        session_id="session-1",
        actor="cole-reflex",
        correlation_id="thread-1",
        source_app="agent-memory-bridge-reflex",
    )
    store.store(
        namespace="cole-core",
        kind="memory",
        title="[[Gotcha]] recall before web",
        content=(
            "record_type: gotcha\n"
            "claim: Check local bridge memory before external search for issue-like prompts.\n"
            "trigger: Issue-like debugging starts from scratch.\n"
            "symptom: The agent wastes time rediscovering prior fixes.\n"
            "fix: Recall project memory and cole-core gotchas before browsing.\n"
            "scope: global\n"
            "confidence: validated"
        ),
        tags=["kind:gotcha", "domain:retrieval", "topic:cross-project-reuse", "project:resume-work"],
        session_id="session-2",
        actor="cole-reflex",
        correlation_id="thread-2",
        source_app="agent-memory-bridge-reflex",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(state_path=tmp_path / "consolidation-state.json"),
    )

    result = engine.run_once()
    domain_notes = store.recall(
        namespace="cole-core",
        tags_any=["kind:domain-note"],
        actor="cole-consolidation",
        limit=10,
    )

    assert result["processed_count"] == 1
    assert domain_notes["count"] == 1
    note = domain_notes["items"][0]
    assert note["source_app"] == "agent-memory-bridge-consolidation"
    assert "record_type: domain-note" in note["content"]
    assert "domain: domain:retrieval" in note["content"]
    assert "support_count: 2" in note["content"]
    assert "topic:fts" in note["content"]
    assert "topic:cross-project-reuse" in note["content"]


def test_consolidation_requires_new_input_before_writing_again(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="cole-core",
        kind="memory",
        title="[[Cole Learn]] Global startup",
        content=(
            "record_type: learn\n"
            "claim: Keep Cole as a system-level operator profile and keep repo AGENTS thin.\n"
            "scope: global\n"
            "confidence: validated"
        ),
        tags=["kind:learn", "domain:orchestration", "topic:startup-protocol", "project:mem-store"],
        session_id="session-3",
        actor="cole-reflex",
        correlation_id="thread-3",
        source_app="agent-memory-bridge-reflex",
    )
    store.store(
        namespace="cole-core",
        kind="memory",
        title="[[Gotcha]] duplicated core drift",
        content=(
            "record_type: gotcha\n"
            "claim: Duplicating full Cole core into each repo AGENTS creates drift and confusion.\n"
            "trigger: Treating AGENTS.md as a system-level startup mechanism.\n"
            "symptom: Global operator rules diverge across repositories.\n"
            "fix: Keep the global operating profile in cole-operator and agentMemoryBridge.\n"
            "scope: global\n"
            "confidence: validated"
        ),
        tags=["kind:gotcha", "domain:orchestration", "topic:startup-protocol", "project:mem-store"],
        session_id="session-4",
        actor="cole-reflex",
        correlation_id="thread-4",
        source_app="agent-memory-bridge-reflex",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(state_path=tmp_path / "consolidation-state.json"),
    )

    first = engine.run_once()
    second = engine.run_once()
    domain_notes = store.recall(
        namespace="cole-core",
        tags_any=["kind:domain-note"],
        actor="cole-consolidation",
        limit=10,
    )

    assert first["processed_count"] == 1
    assert second["processed_count"] == 0
    assert domain_notes["count"] == 1
