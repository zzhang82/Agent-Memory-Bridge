from pathlib import Path

from agent_mem_bridge.reflex import ReflexConfig, ReflexEngine
from agent_mem_bridge.storage import MemoryStore


def test_reflex_promotes_summary_into_learn_and_gotcha(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Codex]] auto closeout 2026-04-04",
        content=(
            "Automatic Codex closeout.\n\n"
            "## Durable Points\n\n"
            "- Assistant outcome: Use one canonical runtime path so the watcher and MCP server share the same database.\n"
            "- Assistant outcome: This fix restored trust in recall after the wrong DB split.\n"
        ),
        tags=["kind:summary", "project:alpha", "source:codex"],
        session_id="session-1",
        actor="codex",
        correlation_id="thread-1",
        source_app="codex-session-watcher",
    )

    reflex = ReflexEngine(
        store,
        ReflexConfig(state_path=tmp_path / "reflex-state.json"),
    )
    result = reflex.run_once()

    learns = store.recall(namespace="global", tags_any=["kind:learn"], limit=10)
    gotchas = store.recall(namespace="global", tags_any=["symptom:wrong-db"], limit=10)

    assert result["processed_count"] >= 2
    assert learns["count"] >= 1
    assert any("record_type: learn" in item["content"] for item in learns["items"])
    assert any("claim: Watcher and MCP server must share the same database." in item["content"] for item in learns["items"])
    assert gotchas["count"] == 1
    assert "fix:canonical-runtime-path" in gotchas["items"][0]["tags"]
    assert "record_type: gotcha" in gotchas["items"][0]["content"]
    assert "fix: use one canonical runtime path and one shared bridge.db" in gotchas["items"][0]["content"]


def test_reflex_creates_domain_note_after_repeated_matches(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    for idx, content in enumerate(
        [
            "- Assistant outcome: Use high reasoning for planning and validation in orchestration work.",
            "- Assistant outcome: Single ownership matters during execution so subagent orchestration avoids contract drift.",
        ],
        start=1,
    ):
        store.store(
            namespace="project:alpha",
            kind="memory",
            title=f"[[Codex]] auto closeout 2026-04-0{idx}",
            content=f"Automatic Codex closeout.\n\n## Durable Points\n\n{content}\n",
            tags=["kind:summary", "project:alpha", "source:codex"],
            session_id=f"session-{idx}",
            actor="codex",
            correlation_id=f"thread-{idx}",
            source_app="codex-session-watcher",
        )

    reflex = ReflexEngine(
        store,
        ReflexConfig(state_path=tmp_path / "reflex-state.json"),
    )
    reflex.run_once()

    domain_notes = store.recall(namespace="global", tags_any=["kind:domain-note"], limit=10)

    assert domain_notes["count"] >= 1
    assert any("domain:orchestration" in item["tags"] for item in domain_notes["items"])
    assert any("record_type: domain-note" in item["content"] for item in domain_notes["items"])


def test_reflex_promotes_machine_first_and_cross_project_memory_rules(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Codex]] auto closeout 2026-04-04",
        content=(
            "Automatic Codex closeout.\n\n"
            "## Durable Points\n\n"
            "- User asked: keep in mind if we talk about stuff in the MCP DB it is you and other agent to read rather than human, so format the content in machine read effective and token effective ways.\n"
            "- Assistant outcome: summaries remain the final artifact and memory turns noisy.\n"
            "- User asked: cole core memory structure should be inside here and not need to put in each project scope.\n"
        ),
        tags=["kind:summary", "project:alpha", "source:codex"],
        session_id="session-2",
        actor="codex",
        correlation_id="thread-2",
        source_app="codex-session-watcher",
    )

    reflex = ReflexEngine(
        store,
        ReflexConfig(state_path=tmp_path / "reflex-state.json"),
    )
    reflex.run_once()

    learns = store.recall(namespace="global", tags_any=["kind:learn"], limit=10)
    gotchas = store.recall(namespace="global", tags_any=["kind:gotcha"], limit=10)

    assert any(
        "claim: Store MCP memory in machine-readable low-token records because agents are the primary readers."
        in item["content"]
        for item in learns["items"]
    )
    assert any("problem:summary-noise" in item["tags"] for item in gotchas["items"])
    assert any("problem:narrative-memory" in item["tags"] for item in gotchas["items"])
    assert any("problem:memory-drift" in item["tags"] for item in gotchas["items"])


def test_reflex_promotes_structured_checkpoint_into_gotcha(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Codex]] checkpoint 2026-04-04",
        content=(
            "Automatic Codex checkpoint.\n\n"
            "## Durable Points\n\n"
            "- Problem: later work can be missing until closeout.\n"
            "- Trigger: active rollout changed after a validated fix.\n"
            "- Fix: write checkpoint summaries during active rollouts.\n"
        ),
        tags=["kind:summary", "project:alpha", "auto-checkpoint", "source:codex"],
        session_id="session-3",
        actor="codex",
        correlation_id="thread-3",
        source_app="codex-session-checkpointer",
    )

    reflex = ReflexEngine(
        store,
        ReflexConfig(state_path=tmp_path / "reflex-state.json"),
    )
    reflex.run_once()

    gotchas = store.recall(namespace="global", tags_any=["kind:gotcha"], limit=10)

    assert any("claim: Later work can be missing until closeout." in item["content"] for item in gotchas["items"])
    assert any("fix: Write checkpoint summaries during active rollouts." in item["content"] for item in gotchas["items"])

