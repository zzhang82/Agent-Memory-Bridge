import sys
from pathlib import Path

from agent_mem_bridge.reflex import ReflexConfig, ReflexEngine
from agent_mem_bridge.storage import MemoryStore


def _gateway_command() -> str:
    fixture = Path(__file__).parent / "fixtures" / "fake_classifier_gateway.py"
    return f'"{sys.executable}" "{fixture}"'


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


def test_reflex_shadow_mode_keeps_fallback_tags_but_reports_divergence(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Codex]] auto closeout 2026-04-05",
        content=(
            "Automatic Codex closeout.\n\n"
            "## Durable Points\n\n"
            "- Assistant outcome: Review handoff should keep explicit queue ownership.\n"
        ),
        tags=["kind:summary", "project:alpha", "source:codex"],
        session_id="session-shadow",
        actor="codex",
        correlation_id="thread-shadow",
        source_app="codex-session-watcher",
    )

    reflex = ReflexEngine(
        store,
        ReflexConfig(
            state_path=tmp_path / "reflex-state.json",
            classifier_mode="shadow",
            classifier_command=_gateway_command(),
        ),
    )
    result = reflex.run_once()

    learns = store.recall(namespace="global", tags_any=["kind:learn"], limit=10)

    assert result["classifier"]["prediction_count"] >= 1
    assert result["classifier"]["divergence_count"] >= 1
    assert result["classifier"]["minimum_confidence"] == 0.6
    assert not any("topic:review-flow" in item["tags"] for item in learns["items"])


def test_reflex_assist_mode_uses_classifier_tags_for_domain_note_matching(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    contents = [
        "- Assistant outcome: Review handoff should keep explicit queue ownership.",
        "- Assistant outcome: API review handoff works better with one approval queue.",
    ]
    for idx, content in enumerate(contents, start=1):
        store.store(
            namespace="project:alpha",
            kind="memory",
            title=f"[[Codex]] auto closeout 2026-04-1{idx}",
            content=f"Automatic Codex closeout.\n\n## Durable Points\n\n{content}\n",
            tags=["kind:summary", "project:alpha", "source:codex"],
            session_id=f"session-assist-{idx}",
            actor="codex",
            correlation_id=f"thread-assist-{idx}",
            source_app="codex-session-watcher",
        )

    reflex = ReflexEngine(
        store,
        ReflexConfig(
            state_path=tmp_path / "reflex-state.json",
            classifier_mode="assist",
            classifier_command=_gateway_command(),
        ),
    )
    result = reflex.run_once()

    domain_notes = store.recall(namespace="global", tags_any=["kind:domain-note"], limit=10)
    learns = store.recall(namespace="global", tags_any=["kind:learn"], limit=10)

    assert result["classifier"]["prediction_count"] >= 2
    assert any("domain:orchestration" in item["tags"] for item in domain_notes["items"])
    assert any("topic:review-flow" in item["tags"] for item in learns["items"])


def test_reflex_assist_mode_ignores_low_confidence_classifier_tags(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Codex]] auto closeout 2026-04-16",
        content=(
            "Automatic Codex closeout.\n\n"
            "## Durable Points\n\n"
            "- Assistant outcome: Punctuation-heavy values.yaml queries need a safe FTS fallback instead of naive token parsing.\n"
        ),
        tags=["kind:summary", "project:alpha", "source:codex"],
        session_id="session-low-confidence",
        actor="codex",
        correlation_id="thread-low-confidence",
        source_app="codex-session-watcher",
    )

    reflex = ReflexEngine(
        store,
        ReflexConfig(
            state_path=tmp_path / "reflex-state.json",
            classifier_mode="assist",
            classifier_command=_gateway_command(),
            classifier_minimum_confidence=0.6,
        ),
    )
    result = reflex.run_once()

    learns = store.recall(namespace="global", tags_any=["kind:learn"], limit=10)

    assert result["classifier"]["prediction_count"] >= 1
    assert result["classifier"]["minimum_confidence"] == 0.6
    assert result["classifier"]["accepted_prediction_count"] == 0
    assert result["classifier"]["filtered_low_confidence_count"] >= 1
    assert any("topic:fts" in item["tags"] for item in learns["items"])
    assert any("domain:agent-memory" in item["tags"] for item in learns["items"])
