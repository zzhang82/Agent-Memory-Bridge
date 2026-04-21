from pathlib import Path

from agent_mem_bridge.consolidation import ConsolidationConfig, ConsolidationEngine
from agent_mem_bridge.storage import MemoryStore


def _store_reflex_record(
    store: MemoryStore,
    *,
    title: str,
    record_type: str,
    claim: str,
    domain: str,
    topic: str,
    session_id: str,
    correlation_id: str,
    trigger: str = "",
    symptom: str = "",
    fix: str = "",
    confidence: str = "observed",
    source_client: str | None = None,
    source_model: str | None = None,
    client_session_id: str | None = None,
    client_workspace: str | None = None,
    client_transport: str | None = None,
) -> None:
    lines = [
        f"record_type: {record_type}",
        f"claim: {claim}",
    ]
    if trigger:
        lines.append(f"trigger: {trigger}")
    if symptom:
        lines.append(f"symptom: {symptom}")
    if fix:
        lines.append(f"fix: {fix}")
    lines.extend(
        [
            "scope: global",
            f"confidence: {confidence}",
        ]
    )
    store.store(
        namespace="global",
        kind="memory",
        title=title,
        content="\n".join(lines),
        tags=[f"kind:{record_type}", domain, topic, "project:mem-store"],
        session_id=session_id,
        actor="bridge-reflex",
        correlation_id=correlation_id,
        source_app="agent-memory-bridge-reflex",
        source_client=source_client,
        source_model=source_model,
        client_session_id=client_session_id,
        client_workspace=client_workspace,
        client_transport=client_transport,
    )


def _items_with_tag(
    store: MemoryStore,
    *,
    tag: str,
    domain: str | None = None,
    actor: str = "bridge-consolidation",
    limit: int = 50,
) -> list[dict]:
    items = store.recall(
        namespace="global",
        actor=actor,
        limit=limit,
    )["items"]
    return [
        item
        for item in items
        if tag in item.get("tags", [])
        and (domain is None or domain in item.get("tags", []))
    ]


def test_consolidation_creates_domain_note_from_recent_learns_and_gotchas(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="global",
        kind="memory",
        title="[[Learn]] Safe FTS fallback",
        content=(
            "record_type: learn\n"
            "claim: Punctuation-heavy queries can break naive FTS recall paths.\n"
            "scope: global\n"
            "confidence: observed"
        ),
        tags=["kind:learn", "domain:retrieval", "topic:fts", "project:mem-store"],
        session_id="session-1",
        actor="bridge-reflex",
        correlation_id="thread-1",
        source_app="agent-memory-bridge-reflex",
        source_client="antigravity",
        source_model="gemini-2.5-pro",
        client_session_id="ag-session-1",
        client_workspace="mem-store",
        client_transport="stdio",
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Gotcha]] recall before web",
        content=(
            "record_type: gotcha\n"
            "claim: Check local bridge memory before external search for issue-like prompts.\n"
            "trigger: Issue-like debugging starts from scratch.\n"
            "symptom: The agent wastes time rediscovering prior fixes.\n"
            "fix: Recall project memory and global gotchas before browsing.\n"
            "scope: global\n"
            "confidence: validated"
        ),
        tags=["kind:gotcha", "domain:retrieval", "topic:cross-project-reuse", "project:resume-work"],
        session_id="session-2",
        actor="bridge-reflex",
        correlation_id="thread-2",
        source_app="agent-memory-bridge-reflex",
        source_client="antigravity",
        source_model="gemini-2.5-pro",
        client_session_id="ag-session-1",
        client_workspace="mem-store",
        client_transport="stdio",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(state_path=tmp_path / "consolidation-state.json"),
    )

    result = engine.run_once()
    domain_notes = store.recall(
        namespace="global",
        tags_any=["kind:domain-note"],
        actor="bridge-consolidation",
        limit=10,
    )

    assert result["processed_count"] == 1
    assert domain_notes["count"] == 1
    note = domain_notes["items"][0]
    assert note["source_app"] == "agent-memory-bridge-consolidation"
    assert "record_type: domain-note" in note["content"]
    assert "domain: domain:retrieval" in note["content"]
    assert "anchor: Punctuation-heavy queries can break naive FTS recall paths." in note["content"]
    assert "rule: Recall project memory and global gotchas before browsing." in note["content"]
    assert "failure_mode: The agent wastes time rediscovering prior fixes." in note["content"]
    assert "epiphany: In retrieval, Recall project memory and global gotchas before browsing because otherwise the agent wastes time rediscovering prior fixes." in note["content"]
    assert "support_count: 2" in note["content"]
    assert "topic:fts" in note["content"]
    assert "topic:cross-project-reuse" in note["content"]
    assert note["source_client"] == "antigravity"
    assert note["source_model"] == "gemini-2.5-pro"
    assert note["client_session_id"] == "ag-session-1"
    assert note["client_workspace"] == "mem-store"
    assert note["client_transport"] == "stdio"


def test_consolidation_requires_new_input_before_writing_again(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="global",
        kind="memory",
        title="[[Learn]] Global startup",
        content=(
            "record_type: learn\n"
            "claim: Keep a system-level operating profile and keep repo AGENTS thin.\n"
            "scope: global\n"
            "confidence: validated"
        ),
        tags=["kind:learn", "domain:orchestration", "topic:startup-protocol", "project:mem-store"],
        session_id="session-3",
        actor="bridge-reflex",
        correlation_id="thread-3",
        source_app="agent-memory-bridge-reflex",
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Gotcha]] duplicated core drift",
        content=(
            "record_type: gotcha\n"
            "claim: Duplicating full shared operating memory into each repo AGENTS creates drift and confusion.\n"
            "trigger: Treating AGENTS.md as a system-level startup mechanism.\n"
            "symptom: Global operator rules diverge across repositories.\n"
            "fix: Keep the global operating profile in one shared bridge namespace.\n"
            "scope: global\n"
            "confidence: validated"
        ),
        tags=["kind:gotcha", "domain:orchestration", "topic:startup-protocol", "project:mem-store"],
        session_id="session-4",
        actor="bridge-reflex",
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
        namespace="global",
        tags_any=["kind:domain-note"],
        actor="bridge-consolidation",
        limit=10,
    )

    assert first["processed_count"] == 1
    assert second["processed_count"] == 0
    assert domain_notes["count"] == 1


def test_consolidation_does_not_treat_fallback_boundary_wording_as_contradiction(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_reflex_record(
        store,
        title="[[Learn]] bundle first",
        record_type="learn",
        claim="Load compact startup records before older operating manuals.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-1",
        correlation_id="thread-1",
    )
    _store_reflex_record(
        store,
        title="[[Learn]] fallback boundary wording",
        record_type="learn",
        claim="But keep old startup references as fallback instead of the default operating payload.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-2",
        correlation_id="thread-2",
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] legacy docs outrank bundle",
        record_type="gotcha",
        claim="Old reference docs should not outrank the compact profile bundle.",
        trigger="Startup retrieval reaches for legacy docs before checking record-tagged bundle rows.",
        symptom="The agent leans on old structure even when the new bundle already covers the decision.",
        fix="Prefer record-tagged profile hits first, then use reference docs only as fallback.",
        confidence="validated",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-3",
        correlation_id="thread-3",
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] bundle-first should lead",
        record_type="gotcha",
        claim="Legacy startup blobs should not displace compact record-tagged guidance.",
        trigger="The system treats old operating docs as the first stop.",
        symptom="Bundle-first startup never gets a clean chance to carry the decision.",
        fix="Prefer record-tagged profile hits first, then use reference docs only as fallback.",
        confidence="validated",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-4",
        correlation_id="thread-4",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(state_path=tmp_path / "consolidation-state.json"),
    )

    first = engine.run_once()
    _store_reflex_record(
        store,
        title="[[Learn]] fallback stays secondary",
        record_type="learn",
        claim="Keep old startup references as fallback rather than the default operating payload.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-5",
        correlation_id="thread-5",
    )
    second = engine.run_once()

    belief_candidates = _items_with_tag(store, tag="kind:belief-candidate", domain="domain:retrieval")
    beliefs = _items_with_tag(store, tag="kind:belief", domain="domain:retrieval")
    concept_notes = _items_with_tag(store, tag="kind:concept-note", domain="domain:retrieval")

    assert first["processed_count"] == 2
    assert second["processed_count"] == 4
    assert belief_candidates
    assert "contradiction_count: 0" in belief_candidates[0]["content"]
    assert "contradiction_reasons: boundary-exempt:fallback-default:1 | no-marker:4" in belief_candidates[0]["content"]
    assert len(beliefs) == 1
    assert "claim: Prefer record-tagged profile hits first, then use reference docs only as fallback." in beliefs[0]["content"]
    assert len(concept_notes) == 1
    assert "record_type: concept-note" in concept_notes[0]["content"]
    assert "depends_on:" in concept_notes[0]["content"]
    assert "relation:depends_on" in concept_notes[0]["tags"]


def test_consolidation_does_not_treat_manual_policy_boundary_wording_as_contradiction(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_reflex_record(
        store,
        title="[[Learn]] belief bridge stays explicit",
        record_type="learn",
        claim="Use belief_candidate as the bridge between compressed domain patterns and manual core-policy review.",
        domain="domain:startup-protocol",
        topic="topic:belief-candidate",
        session_id="session-1",
        correlation_id="thread-1",
    )
    _store_reflex_record(
        store,
        title="[[Learn]] policy boundary stays manual",
        record_type="learn",
        claim="However, keep core policy manual while belief evidence is still accumulating.",
        domain="domain:startup-protocol",
        topic="topic:belief-candidate",
        session_id="session-2",
        correlation_id="thread-2",
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] bridge before policy rewrite",
        record_type="gotcha",
        claim="Skipping the belief bridge makes policy updates feel premature.",
        trigger="Compressed patterns are treated like final policy before enough evidence accumulates.",
        symptom="Core policy review gets noisy because every pattern looks policy-ready.",
        fix="Use belief_candidate as the bridge between compressed domain patterns and manual core-policy review.",
        confidence="validated",
        domain="domain:startup-protocol",
        topic="topic:belief-candidate",
        session_id="session-3",
        correlation_id="thread-3",
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] bridge narrows policy changes",
        record_type="gotcha",
        claim="Policy changes become cleaner when belief_candidate absorbs pattern churn first.",
        trigger="Every compressed pattern competes to rewrite policy directly.",
        symptom="Manual policy review is flooded with unstable proposals.",
        fix="Use belief_candidate as the bridge between compressed domain patterns and manual core-policy review.",
        confidence="validated",
        domain="domain:startup-protocol",
        topic="topic:belief-candidate",
        session_id="session-4",
        correlation_id="thread-4",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(state_path=tmp_path / "consolidation-state.json"),
    )

    first = engine.run_once()
    _store_reflex_record(
        store,
        title="[[Learn]] manual review remains downstream",
        record_type="learn",
        claim="Keep core policy review manual even after a belief has stabilized.",
        domain="domain:startup-protocol",
        topic="topic:belief-candidate",
        session_id="session-5",
        correlation_id="thread-5",
    )
    second = engine.run_once()

    belief_candidates = _items_with_tag(store, tag="kind:belief-candidate", domain="domain:startup-protocol")
    beliefs = _items_with_tag(store, tag="kind:belief", domain="domain:startup-protocol")
    concept_notes = _items_with_tag(store, tag="kind:concept-note", domain="domain:startup-protocol")

    assert first["processed_count"] == 2
    assert second["processed_count"] == 4
    assert belief_candidates
    assert "contradiction_count: 0" in belief_candidates[0]["content"]
    assert "contradiction_reasons: boundary-exempt:manual-policy:1 | no-marker:4" in belief_candidates[0]["content"]
    assert len(beliefs) == 1
    assert "claim: Use belief_candidate as the bridge between compressed domain patterns and manual core-policy review." in beliefs[0]["content"]
    assert len(concept_notes) == 1
    assert "record_type: concept-note" in concept_notes[0]["content"]
    assert "relation:depends_on" in concept_notes[0]["tags"]


def test_consolidation_does_not_treat_project_vs_global_scope_wording_as_contradiction(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_reflex_record(
        store,
        title="[[Learn]] project lessons stay local",
        record_type="learn",
        claim="Write project-specific durable lessons to project memory.",
        domain="domain:memory-governance",
        topic="topic:writeback",
        session_id="session-1",
        correlation_id="thread-1",
    )
    _store_reflex_record(
        store,
        title="[[Learn]] global core stays narrow",
        record_type="learn",
        claim="However, keep project-specific workflow rules in project memory while the global core stays reserved for durable operating lessons.",
        domain="domain:memory-governance",
        topic="topic:writeback",
        session_id="session-2",
        correlation_id="thread-2",
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] project drift in global core",
        record_type="gotcha",
        claim="Writing repo-specific workflow rules into the global core creates drift.",
        trigger="A project lesson is stored into a cross-project operating namespace.",
        symptom="The global core starts carrying workflow details that belong to one repository.",
        fix="Write project-specific durable lessons to project memory, and keep global core for durable operating lessons.",
        confidence="validated",
        domain="domain:memory-governance",
        topic="topic:writeback",
        session_id="session-3",
        correlation_id="thread-3",
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] global core gets noisy",
        record_type="gotcha",
        claim="The global core gets noisy when project-specific workflow rules accumulate there.",
        trigger="Cross-project memory is used as the default landing zone for project-specific lessons.",
        symptom="Startup carries local workflow baggage that does not belong in every session.",
        fix="Write project-specific durable lessons to project memory, and keep global core for durable operating lessons.",
        confidence="validated",
        domain="domain:memory-governance",
        topic="topic:writeback",
        session_id="session-4",
        correlation_id="thread-4",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(state_path=tmp_path / "consolidation-state.json"),
    )

    first = engine.run_once()
    _store_reflex_record(
        store,
        title="[[Learn]] writeback keeps scope clean",
        record_type="learn",
        claim="Keep project-specific workflow rules in project memory instead of teaching them to the global core.",
        domain="domain:memory-governance",
        topic="topic:writeback",
        session_id="session-5",
        correlation_id="thread-5",
    )
    second = engine.run_once()

    belief_candidates = _items_with_tag(store, tag="kind:belief-candidate", domain="domain:memory-governance")
    beliefs = _items_with_tag(store, tag="kind:belief", domain="domain:memory-governance")
    concept_notes = _items_with_tag(store, tag="kind:concept-note", domain="domain:memory-governance")

    assert first["processed_count"] == 2
    assert second["processed_count"] == 4
    assert belief_candidates
    assert "contradiction_count: 0" in belief_candidates[0]["content"]
    assert "contradiction_reasons: boundary-exempt:project-vs-global:1 | no-marker:4" in belief_candidates[0]["content"]
    assert len(beliefs) == 1
    assert "claim: Write project-specific durable lessons to project memory, and keep global core for durable operating lessons." in beliefs[0]["content"]
    assert len(concept_notes) == 1
    assert "record_type: concept-note" in concept_notes[0]["content"]
    assert "relation:depends_on" in concept_notes[0]["tags"]


def test_consolidation_clears_mixed_origin_metadata(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="global",
        kind="memory",
        title="[[Learn]] Review queue ownership",
        content=(
            "record_type: learn\n"
            "claim: Review handoff should keep explicit queue ownership.\n"
            "scope: global\n"
            "confidence: observed"
        ),
        tags=["kind:learn", "domain:orchestration", "topic:review-flow", "project:mem-store"],
        session_id="session-a",
        actor="bridge-reflex",
        correlation_id="thread-a",
        source_app="agent-memory-bridge-reflex",
        source_client="codex",
        source_model="gpt-5.4",
        client_session_id="codex-session",
        client_workspace="mem-store",
        client_transport="stdio",
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Gotcha]] Review queue ambiguity",
        content=(
            "record_type: gotcha\n"
            "claim: Review handoff without explicit ownership creates queue ambiguity.\n"
            "trigger: Multiple reviewers believe someone else owns the queue.\n"
            "symptom: Review work stalls.\n"
            "fix: Assign one explicit owner for each review queue.\n"
            "scope: global\n"
            "confidence: validated"
        ),
        tags=["kind:gotcha", "domain:orchestration", "topic:review-flow", "project:mem-store"],
        session_id="session-b",
        actor="bridge-reflex",
        correlation_id="thread-b",
        source_app="agent-memory-bridge-reflex",
        source_client="antigravity",
        source_model="gemini-2.5-pro",
        client_session_id="ag-session",
        client_workspace="mem-store",
        client_transport="stdio",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(state_path=tmp_path / "consolidation-state.json"),
    )

    result = engine.run_once()
    domain_notes = store.recall(
        namespace="global",
        tags_any=["kind:domain-note", "domain:orchestration"],
        actor="bridge-consolidation",
        limit=10,
    )

    assert result["processed_count"] == 1
    assert domain_notes["count"] == 1
    note = domain_notes["items"][0]
    assert "anchor: Review handoff should keep explicit queue ownership." in note["content"]
    assert "rule: Assign one explicit owner for each review queue." in note["content"]
    assert "failure_mode: Review work stalls." in note["content"]
    assert note["source_client"] is None
    assert note["source_model"] is None
    assert note["client_session_id"] is None


def test_consolidation_emits_belief_candidate_when_support_is_strong(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_reflex_record(
        store,
        title="[[Learn]] recall bundle first",
        record_type="learn",
        claim="Load compact startup records before older operating manuals.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-10",
        correlation_id="thread-10",
        source_client="antigravity",
        source_model="gemini-2.5-pro",
        client_session_id="ag-session-2",
        client_workspace="mem-store",
        client_transport="stdio",
    )
    _store_reflex_record(
        store,
        title="[[Learn]] compact startup stays small",
        record_type="learn",
        claim="Keep startup focused on core-policy, persona, soul, and project memory.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-11",
        correlation_id="thread-11",
        source_client="antigravity",
        source_model="gemini-2.5-pro",
        client_session_id="ag-session-2",
        client_workspace="mem-store",
        client_transport="stdio",
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] startup falls back too early",
        record_type="gotcha",
        claim="Old reference docs should not outrank the compact profile bundle.",
        trigger="Startup retrieval reaches for legacy docs before checking record-tagged bundle rows.",
        symptom="The agent leans on old structure even when the new bundle already covers the decision.",
        fix="Prefer record-tagged profile hits first, then use reference docs only as fallback.",
        confidence="validated",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-12",
        correlation_id="thread-12",
        source_client="antigravity",
        source_model="gemini-2.5-pro",
        client_session_id="ag-session-2",
        client_workspace="mem-store",
        client_transport="stdio",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(
            state_path=tmp_path / "consolidation-state.json",
            belief_candidate_min_support=3,
        ),
    )

    result = engine.run_once()
    belief_candidates = store.recall(
        namespace="global",
        tags_any=["kind:belief-candidate"],
        actor="bridge-consolidation",
        limit=10,
    )

    assert result["processed_count"] == 2
    assert belief_candidates["count"] == 1
    candidate = belief_candidates["items"][0]
    assert "record_type: belief-candidate" in candidate["content"]
    assert "domain: domain:retrieval" in candidate["content"]
    assert "support_count: 3" in candidate["content"]
    assert "distinct_session_count: 3" in candidate["content"]
    assert "contradiction_count: 0" in candidate["content"]
    assert "confidence: candidate" in candidate["content"]
    assert "evidence_refs:" in candidate["content"]
    assert candidate["source_client"] == "antigravity"
    assert candidate["source_model"] == "gemini-2.5-pro"
    assert candidate["client_session_id"] == "ag-session-2"
    assert candidate["client_workspace"] == "mem-store"
    assert candidate["client_transport"] == "stdio"


def test_belief_candidate_becomes_tentative_with_contradiction_markers(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="global",
        kind="memory",
        title="[[Learn]] one runtime path",
        content=(
            "record_type: learn\n"
            "claim: Keep one canonical runtime path for bridge state.\n"
            "scope: global\n"
            "confidence: observed"
        ),
        tags=["kind:learn", "domain:memory-bridge", "topic:runtime-path", "project:mem-store"],
        session_id="session-a",
        actor="bridge-reflex",
        correlation_id="thread-a",
        source_app="agent-memory-bridge-reflex",
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Learn]] fallback windows",
        content=(
            "record_type: learn\n"
            "claim: However, keep a temporary fallback path during cutover windows.\n"
            "scope: global\n"
            "confidence: observed"
        ),
        tags=["kind:learn", "domain:memory-bridge", "topic:runtime-path", "project:mem-store"],
        session_id="session-b",
        actor="bridge-reflex",
        correlation_id="thread-b",
        source_app="agent-memory-bridge-reflex",
    )
    store.store(
        namespace="global",
        kind="memory",
        title="[[Gotcha]] split store drift",
        content=(
            "record_type: gotcha\n"
            "claim: Split runtime paths break trust in recall.\n"
            "trigger: Interactive and automation paths write to different stores.\n"
            "symptom: Recall silently misses recent memories.\n"
            "fix: Move back to one canonical runtime path after the cutover window closes.\n"
            "scope: global\n"
            "confidence: validated"
        ),
        tags=["kind:gotcha", "domain:memory-bridge", "topic:runtime-path", "project:mem-store"],
        session_id="session-c",
        actor="bridge-reflex",
        correlation_id="thread-c",
        source_app="agent-memory-bridge-reflex",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(
            state_path=tmp_path / "consolidation-state.json",
            belief_candidate_min_support=3,
        ),
    )

    engine.run_once()
    belief_candidates = store.recall(
        namespace="global",
        tags_any=["kind:belief-candidate", "domain:memory-bridge"],
        actor="bridge-consolidation",
        limit=10,
    )

    assert belief_candidates["count"] >= 1
    candidate = next(
        item for item in belief_candidates["items"] if "domain: domain:memory-bridge" in item["content"]
    )
    assert "contradiction_count: 1" in candidate["content"]
    assert "contradiction_reasons: marker-contrast:1 | no-marker:2" in candidate["content"]
    assert "confidence: tentative" in candidate["content"]


def test_belief_promotes_after_stable_multi_session_candidates(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    kwargs = {
        "source_client": "antigravity",
        "source_model": "gemini-2.5-pro",
        "client_session_id": "ag-session-3",
        "client_workspace": "mem-store",
        "client_transport": "stdio",
    }
    _store_reflex_record(
        store,
        title="[[Learn]] bundle first",
        record_type="learn",
        claim="Load compact startup records before older operating manuals.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-20",
        correlation_id="thread-20",
        **kwargs,
    )
    _store_reflex_record(
        store,
        title="[[Learn]] startup stays compact",
        record_type="learn",
        claim="Keep startup focused on core-policy, persona, soul, and project memory.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-21",
        correlation_id="thread-21",
        **kwargs,
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] legacy docs outrank bundle",
        record_type="gotcha",
        claim="Old reference docs should not outrank the compact profile bundle.",
        trigger="Startup retrieval reaches for legacy docs before checking record-tagged bundle rows.",
        symptom="The agent leans on old structure even when the new bundle already covers the decision.",
        fix="Prefer record-tagged profile hits first, then use reference docs only as fallback.",
        confidence="validated",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-22",
        correlation_id="thread-22",
        **kwargs,
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] startup blob hides new layer",
        record_type="gotcha",
        claim="Legacy startup blobs should not displace compact record-tagged guidance.",
        trigger="The system treats old operating docs as the first stop.",
        symptom="Bundle-first startup never gets a clean chance to carry the decision.",
        fix="Prefer record-tagged profile hits first, then use reference docs only as fallback.",
        confidence="validated",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-23",
        correlation_id="thread-23",
        **kwargs,
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(state_path=tmp_path / "consolidation-state.json"),
    )

    first = engine.run_once()
    beliefs_after_first = _items_with_tag(store, tag="kind:belief", domain="domain:retrieval")

    _store_reflex_record(
        store,
        title="[[Learn]] fallback stays secondary",
        record_type="learn",
        claim="Keep old startup references as fallback instead of the default operating payload.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-24",
        correlation_id="thread-24",
        **kwargs,
    )

    second = engine.run_once()
    beliefs_after_second = _items_with_tag(store, tag="kind:belief", domain="domain:retrieval")
    concept_notes_after_second = _items_with_tag(store, tag="kind:concept-note", domain="domain:retrieval")

    assert first["processed_count"] == 2
    assert len(beliefs_after_first) == 0
    assert second["processed_count"] == 4
    assert len(beliefs_after_second) == 1
    assert len(concept_notes_after_second) == 1
    belief = beliefs_after_second[0]
    concept_note = concept_notes_after_second[0]
    assert "record_type: belief" in belief["content"]
    assert "support_count: 5" in belief["content"]
    assert "distinct_session_count: 5" in belief["content"]
    assert "contradiction_count: 0" in belief["content"]
    assert "status: active" in belief["content"]
    assert "derived_from_candidate_id:" in belief["content"]
    assert belief["source_client"] == "antigravity"
    assert belief["source_model"] == "gemini-2.5-pro"
    assert belief["client_session_id"] == "ag-session-3"
    assert "record_type: concept-note" in concept_note["content"]
    assert "depends_on:" in concept_note["content"]
    assert "epiphany:" in concept_note["content"]
    assert "relation:depends_on" in concept_note["tags"]


def test_same_session_burst_does_not_promote_belief(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    for index in range(4):
        _store_reflex_record(
            store,
            title=f"[[Learn]] burst {index}",
            record_type="learn",
            claim=f"Keep startup rule #{index} compact and inspectable.",
            domain="domain:agent-memory",
            topic="topic:startup-protocol",
            session_id="burst-session",
            correlation_id=f"burst-{index}",
        )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(state_path=tmp_path / "consolidation-state.json"),
    )

    engine.run_once()
    _store_reflex_record(
        store,
        title="[[Gotcha]] burst follow-up",
        record_type="gotcha",
        claim="A single session burst should not become a durable belief.",
        trigger="Many similar observations arrive in one short burst.",
        symptom="The system overfits one episode.",
        fix="Require repeated support across distinct sessions before promoting belief.",
        confidence="validated",
        domain="domain:agent-memory",
        topic="topic:startup-protocol",
        session_id="burst-session",
        correlation_id="burst-follow-up",
    )
    engine.run_once()

    beliefs = _items_with_tag(store, tag="kind:belief", domain="domain:agent-memory")

    assert len(beliefs) == 0


def test_contradictions_block_belief_promotion(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_reflex_record(
        store,
        title="[[Learn]] canonical path",
        record_type="learn",
        claim="Keep one canonical runtime path for bridge state.",
        domain="domain:memory-bridge",
        topic="topic:runtime-path",
        session_id="session-30",
        correlation_id="thread-30",
    )
    _store_reflex_record(
        store,
        title="[[Learn]] temporary window",
        record_type="learn",
        claim="However, keep a temporary fallback path during cutover windows.",
        domain="domain:memory-bridge",
        topic="topic:runtime-path",
        session_id="session-31",
        correlation_id="thread-31",
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] split path drift",
        record_type="gotcha",
        claim="Split runtime paths break trust in recall.",
        trigger="Interactive and automation paths write to different stores.",
        symptom="Recall silently misses recent memories.",
        fix="Move back to one canonical runtime path after the cutover window closes.",
        confidence="validated",
        domain="domain:memory-bridge",
        topic="topic:runtime-path",
        session_id="session-32",
        correlation_id="thread-32",
    )
    _store_reflex_record(
        store,
        title="[[Learn]] runtime trust",
        record_type="learn",
        claim="Trust in recall depends on a shared runtime path.",
        domain="domain:memory-bridge",
        topic="topic:runtime-path",
        session_id="session-33",
        correlation_id="thread-33",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(state_path=tmp_path / "consolidation-state.json"),
    )

    engine.run_once()
    _store_reflex_record(
        store,
        title="[[Learn]] post-cutover cleanup",
        record_type="learn",
        claim="Re-unify runtime paths after temporary cutover windows close.",
        domain="domain:memory-bridge",
        topic="topic:runtime-path",
        session_id="session-34",
        correlation_id="thread-34",
    )
    engine.run_once()

    beliefs = _items_with_tag(store, tag="kind:belief", domain="domain:memory-bridge")

    assert len(beliefs) == 0


def test_stale_candidates_do_not_promote_belief(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_reflex_record(
        store,
        title="[[Learn]] bundle first retrieval",
        record_type="learn",
        claim="Load bundle-first retrieval before older startup manuals.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-40",
        correlation_id="thread-40",
    )
    _store_reflex_record(
        store,
        title="[[Learn]] compact startup wins",
        record_type="learn",
        claim="Keep compact startup guidance ahead of legacy reference startup.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-41",
        correlation_id="thread-41",
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] legacy docs outrank bundle",
        record_type="gotcha",
        claim="Legacy startup docs should not outrank the compact profile bundle.",
        trigger="Startup retrieval reaches for old manuals before record-tagged rows.",
        symptom="The agent keeps falling back to legacy startup material.",
        fix="Prefer bundle-first retrieval, then use legacy docs only as fallback.",
        confidence="validated",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-42",
        correlation_id="thread-42",
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] startup blob hides bundle",
        record_type="gotcha",
        claim="Legacy startup blobs should not displace compact record-tagged guidance.",
        trigger="The system treats old operating docs as the first stop.",
        symptom="Bundle-first startup never gets a clean chance to carry the decision.",
        fix="Prefer bundle-first retrieval, then use legacy docs only as fallback.",
        confidence="validated",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-43",
        correlation_id="thread-43",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(
            state_path=tmp_path / "consolidation-state.json",
            belief_freshness_days=7,
        ),
    )

    engine.run_once()
    first_candidate = _items_with_tag(store, tag="kind:belief-candidate", domain="domain:retrieval")[0]
    with store._connect() as conn:
        conn.execute(
            "UPDATE memories SET created_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", first_candidate["id"]),
        )
        conn.commit()

    _store_reflex_record(
        store,
        title="[[Learn]] fallback stays secondary",
        record_type="learn",
        claim="Keep old startup references as fallback instead of the default operating payload.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="session-44",
        correlation_id="thread-44",
    )
    engine.run_once()

    beliefs = _items_with_tag(store, tag="kind:belief", domain="domain:retrieval")

    assert len(beliefs) == 0


def test_unstable_candidate_hash_blocks_belief_promotion(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    _store_reflex_record(
        store,
        title="[[Learn]] verify before done",
        record_type="learn",
        claim="Verify behavior before calling work done.",
        domain="domain:agent-memory",
        topic="topic:verification",
        session_id="session-50",
        correlation_id="thread-50",
    )
    _store_reflex_record(
        store,
        title="[[Gotcha]] skipped verification",
        record_type="gotcha",
        claim="Skipping verification weakens trust in memory.",
        trigger="The agent marks work complete before checking behavior.",
        symptom="Later sessions rediscover broken assumptions.",
        fix="Verify the actual behavior before calling work done.",
        confidence="validated",
        domain="domain:agent-memory",
        topic="topic:verification",
        session_id="session-51",
        correlation_id="thread-51",
    )
    _store_reflex_record(
        store,
        title="[[Learn]] preserve proof",
        record_type="learn",
        claim="Keep executable proof close to the claim.",
        domain="domain:agent-memory",
        topic="topic:verification",
        session_id="session-52",
        correlation_id="thread-52",
    )
    _store_reflex_record(
        store,
        title="[[Learn]] trust through verification",
        record_type="learn",
        claim="Trust grows when behavior is verified instead of guessed.",
        domain="domain:agent-memory",
        topic="topic:verification",
        session_id="session-53",
        correlation_id="thread-53",
    )

    engine = ConsolidationEngine(
        store=store,
        config=ConsolidationConfig(state_path=tmp_path / "consolidation-state.json"),
    )

    engine.run_once()
    _store_reflex_record(
        store,
        title="[[Gotcha]] changed rule",
        record_type="gotcha",
        claim="Proof-first discipline beats intuition-only review.",
        trigger="Review focuses on prose instead of behavior.",
        symptom="A different operating rule wins the second cycle.",
        fix="Use executable proof as the first validation boundary.",
        confidence="validated",
        domain="domain:agent-memory",
        topic="topic:verification",
        session_id="session-54",
        correlation_id="thread-54",
    )
    engine.run_once()

    beliefs = _items_with_tag(store, tag="kind:belief", domain="domain:agent-memory")

    assert len(beliefs) == 0
