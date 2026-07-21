from __future__ import annotations

from pathlib import Path

from agent_mem_bridge.procedure_governance import parse_procedure_artifact
from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.task_brief import build_task_brief_report, render_task_brief_markdown
from agent_mem_bridge.task_memory import assemble_task_memory

NAMESPACE = "project:governed-change"
AS_OF = "2026-07-15T12:00:00+00:00"


def _store_with_id(store: MemoryStore, memory_id: str, **kwargs: object) -> dict[str, object]:
    original_new_id = store._new_id
    store._new_id = lambda: memory_id
    try:
        return store.store(**kwargs)  # type: ignore[arg-type]
    finally:
        store._new_id = original_new_id


def test_procedure_domains_are_normalized_and_legacy_scope_stays_eligible() -> None:
    scoped = parse_procedure_artifact(
        "record_type: procedure\n"
        "goal: Run a release cutover.\n"
        "applies_to_domains: domain:Release | deployment_ops\n"
        "steps: verify | deploy\n",
        task_domain="domain:skills",
    )
    legacy = parse_procedure_artifact(
        "record_type: procedure\ngoal: Run a legacy cutover.\nsteps: verify | deploy\n",
        task_domain="release",
    )

    assert scoped["applies_to_domains"] == ["release", "deployment-ops"]
    assert scoped["governance"]["eligible"] is False
    assert scoped["governance"]["ineligible_reason"] == "task_domain_mismatch:skills"
    assert legacy["governance"]["eligible"] is True
    assert "unscoped-procedure-domains" in legacy["governance"]["warnings"]


def test_procedure_scope_is_inferred_only_from_exact_domain_tags() -> None:
    inferred = parse_procedure_artifact(
        "record_type: procedure\ngoal: Run a release cutover.\nsteps: verify | deploy\n",
        tags=["kind:procedure", "domain:Release", "topic:domain:skills"],
        task_domain="skills",
    )
    unscoped = parse_procedure_artifact(
        "record_type: procedure\ngoal: Run a legacy cutover.\nsteps: verify | deploy\n",
        tags=["kind:procedure", "topic:domain:release"],
        task_domain="skills",
    )

    assert inferred["applies_to_domains"] == ["release"]
    assert inferred["governance"]["scope_source"] == "domain-tags"
    assert inferred["governance"]["eligible"] is False
    assert inferred["governance"]["ineligible_reason"] == "task_domain_mismatch:skills"
    assert "procedure-domains-inferred-from-tags" in inferred["governance"]["warnings"]
    assert unscoped["applies_to_domains"] == []
    assert unscoped["governance"]["scope_source"] == "unscoped"
    assert unscoped["governance"]["eligible"] is True
    assert "unscoped-procedure-domains" in unscoped["governance"]["warnings"]


def test_tag_inferred_scope_suppresses_task_domain_mismatch(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    procedure = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] tag-scoped release cutover",
        content=("record_type: procedure\ngoal: Run the tag-scoped release cutover.\nsteps: verify | deploy\n"),
        tags=["kind:procedure", "domain:release", "topic:tag-scoped-cutover"],
    )

    report = assemble_task_memory(
        store,
        query="tag-scoped release cutover",
        project_namespace=NAMESPACE,
        as_of=AS_OF,
        task_domain="skills",
    )

    assert report["procedure_hits"] == []
    assert any(
        item["id"] == procedure["id"] and item["reason"] == "task_domain_mismatch:skills"
        for item in report["suppressed_items"]
    )


def test_task_memory_uses_fixed_as_of_and_rejects_explicit_domain_mismatch(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    procedure = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] bounded release cutover",
        content=(
            "record_type: procedure\n"
            "goal: Run the bounded release cutover.\n"
            "applies_to_domains: release | deployment\n"
            "steps: verify | deploy\n"
            "valid_from: 2026-01-01T00:00:00+00:00\n"
            "valid_until: 2026-12-31T23:59:59+00:00\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:cutover"],
    )

    current = assemble_task_memory(
        store,
        query="bounded release cutover",
        project_namespace=NAMESPACE,
        as_of=AS_OF,
        task_domain="release",
    )
    mismatched = assemble_task_memory(
        store,
        query="bounded release cutover",
        project_namespace=NAMESPACE,
        as_of=AS_OF,
        task_domain="skills",
    )
    expired = assemble_task_memory(
        store,
        query="bounded release cutover",
        project_namespace=NAMESPACE,
        as_of="2027-01-01T00:00:00Z",
        task_domain="release",
    )

    assert [item["id"] for item in current["procedure_hits"]] == [procedure["id"]]
    assert current["as_of"] == AS_OF
    assert current["task_domain"] == "release"
    assert mismatched["procedure_hits"] == []
    assert any(
        item["id"] == procedure["id"] and item["reason"] == "task_domain_mismatch:skills"
        for item in mismatched["suppressed_items"]
    )
    assert any(
        item["id"] == procedure["id"] and item["reason"] == "validity:expired" for item in expired["suppressed_items"]
    )


def test_degraded_lineage_and_unresolved_dependencies_require_review(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    degraded = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] degraded release handoff",
        content=(
            "record_type: procedure\n"
            "goal: Run a degraded release handoff.\n"
            "steps: verify | hand off\n"
            "lineage_status: degraded\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )
    unresolved = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] dependency-incomplete release handoff",
        content=(
            "record_type: procedure\n"
            "goal: Run a dependency-incomplete release handoff.\n"
            "steps: verify | hand off\n"
            "depends_on: missing-release-approval\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )

    report = build_task_brief_report(
        store,
        query="release handoff",
        namespace=NAMESPACE,
        generated_at=AS_OF,
        as_of=AS_OF,
        task_domain="release",
    )

    used_ids = {item["source_record_id"] for item in report["sections"]["used"]}
    review_reasons = {reason for item in report["sections"]["needs_review"] for reason in item["reason_codes"]}
    assert degraded["id"] not in used_ids
    assert unresolved["id"] not in used_ids
    assert {"lineage_status:degraded", "depends_on:unresolved"} <= review_reasons
    assert "## Needs Review" in render_task_brief_markdown(report)


def test_persisted_degraded_lineage_surfaces_missing_source_in_needs_review(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    source = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="Release approval source",
        content="record_type: learn\nclaim: The release approval is current.\n",
        tags=["kind:learn", "domain:release"],
    )
    procedure = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] source-backed release handoff",
        content=(
            "record_type: procedure\n"
            "goal: Run a source-backed release handoff.\n"
            "applies_to_domains: release\n"
            "steps: verify approval | hand off\n"
            f"depends_on: {source['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:handoff"],
    )
    store.forget(source["id"])

    report = build_task_brief_report(
        store,
        query="source-backed release handoff",
        namespace=NAMESPACE,
        generated_at=AS_OF,
        as_of=AS_OF,
        task_domain="release",
    )

    review_item = next(
        item for item in report["sections"]["needs_review"] if item["source_record_id"] == procedure["id"]
    )
    assert review_item["reason_codes"] == ["lineage_status:degraded"]
    assert review_item["lineage_issue_count"] == 1
    assert review_item["missing_lineage_record_ids"] == [source["id"]]


def test_current_state_change_suppresses_procedure_and_retains_corrective_evidence(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    obsolete = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] deploy through the retired queue",
        content=(
            "record_type: procedure\n"
            "goal: Deploy through the retired queue.\n"
            "applies_to_domains: release\n"
            "steps: open retired queue | deploy\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:deploy"],
    )
    current = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[State Change]] release queue retired",
        content=(
            "record_type: state-change\n"
            "current_state: The retired queue is closed; use the governed deployment queue.\n"
            f"supersedes: {obsolete['id']}\n"
        ),
        tags=["kind:state-change", "domain:release", "topic:deploy"],
    )

    task_memory = assemble_task_memory(
        store,
        query="release deploy queue",
        project_namespace=NAMESPACE,
        as_of=AS_OF,
        task_domain="release",
    )
    brief = build_task_brief_report(
        store,
        query="release deploy queue",
        namespace=NAMESPACE,
        generated_at=AS_OF,
        as_of=AS_OF,
        task_domain="release",
    )

    assert task_memory["procedure_hits"] == []
    assert [item["id"] for item in task_memory["corrective_items"]] == [current["id"]]
    assert any(
        item["id"] == obsolete["id"] and item["reason"] == "superseded" and item["by_record_type"] == "state-change"
        for item in task_memory["suppressed_items"]
    )
    corrective = next(item for item in brief["sections"]["needs_review"] if item["source_record_id"] == current["id"])
    assert corrective["selected_as"] == "corrective-evidence"
    assert corrective["corrective_evidence"] == ("The retired queue is closed; use the governed deployment queue.")
    assert "use the governed deployment queue" in render_task_brief_markdown(brief)


def test_transitive_supersession_retains_only_latest_bounded_generation(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    first = _store_with_id(
        store,
        "release-generation-a",
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] release generation A",
        content=("record_type: procedure\ngoal: Run adversarial release generation A.\nsteps: verify A | deploy A\n"),
        tags=["kind:procedure", "domain:release", "topic:bounded-generation"],
    )
    second = _store_with_id(
        store,
        "release-generation-b",
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] release generation B",
        content=(
            "record_type: procedure\n"
            "goal: Run adversarial release generation B.\n"
            "steps: verify B | deploy B\n"
            f"supersedes: {first['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:bounded-generation"],
    )
    third = _store_with_id(
        store,
        "release-generation-c",
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] release generation C",
        content=(
            "record_type: procedure\n"
            "goal: Run terminal-current-marker generation C.\n"
            "steps: verify C | deploy C\n"
            f"supersedes: {second['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:bounded-generation"],
    )

    report = assemble_task_memory(
        store,
        query="terminal-current-marker",
        project_namespace=NAMESPACE,
        as_of=AS_OF,
        task_domain="release",
    )

    assert [item["id"] for item in report["procedure_hits"]] == [third["id"]]
    suppressed = {item["id"]: (item["reason"], item["by_id"]) for item in report["suppressed_items"]}
    assert suppressed[first["id"]] == ("superseded", second["id"])
    assert suppressed[second["id"]] == ("superseded", third["id"])


def test_forgotten_superseder_tombstone_keeps_predecessor_in_needs_review(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    predecessor_id = "forgotten-chain-predecessor"
    superseder_id = "forgotten-chain-superseder"
    predecessor = _store_with_id(
        store,
        predecessor_id,
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] predecessor must not resurrect",
        content=(
            "record_type: procedure\n"
            "goal: Keep forgotten supersession review-safe.\n"
            "steps: verify lineage | stop for review\n"
            "lineage_status: intact\n"
            f"depends_on: {superseder_id}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:forgotten-superseder"],
    )
    superseder = _store_with_id(
        store,
        superseder_id,
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] current superseder",
        content=(
            "record_type: procedure\n"
            "goal: Keep forgotten supersession review-safe.\n"
            "steps: use current guidance | deploy\n"
            f"supersedes: {predecessor_id}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:forgotten-superseder"],
    )
    store.forget(str(superseder["id"]))

    report = build_task_brief_report(
        store,
        query="forgotten supersession review-safe",
        namespace=NAMESPACE,
        generated_at=AS_OF,
        as_of=AS_OF,
        task_domain="release",
    )

    used_ids = {item["source_record_id"] for item in report["sections"]["used"]}
    predecessor_review = next(
        item for item in report["sections"]["needs_review"] if item["source_record_id"] == predecessor["id"]
    )
    tombstone_review = next(
        item for item in report["sections"]["needs_review"] if item["source_record_id"] == superseder["id"]
    )
    assert predecessor["id"] not in used_ids
    assert predecessor_review["reason_codes"] == ["lineage_status:degraded"]
    assert predecessor_review["missing_lineage_record_ids"] == [superseder["id"]]
    assert tombstone_review["reason_codes"] == ["unresolved_relation_target", "forgotten"]
    assert tombstone_review["tombstone_cause"] == "explicit_forget"
    assert "content" not in tombstone_review


def test_degraded_dependency_ineligibility_propagates_through_chain(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    root = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="Degraded approval root",
        content="record_type: learn\nclaim: Approval evidence requires review.\n",
        tags=["kind:learn", "domain:release", "topic:dependency-chain"],
    )
    middle = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="Release approval wrapper",
        content=(f"record_type: learn\nclaim: Wrapper depends on approval evidence.\ndepends_on: {root['id']}\n"),
        tags=["kind:learn", "domain:release", "topic:dependency-chain"],
    )
    procedure = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] dependency-chain release",
        content=(
            "record_type: procedure\n"
            "goal: Run the dependency-chain release.\n"
            "steps: verify approval | deploy\n"
            f"depends_on: {middle['id']}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:dependency-chain"],
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE memories SET lineage_status = 'degraded' WHERE id = ?",
            (root["id"],),
        )
        conn.commit()

    report = build_task_brief_report(
        store,
        query="dependency-chain release",
        namespace=NAMESPACE,
        generated_at=AS_OF,
        as_of=AS_OF,
        task_domain="release",
    )

    used_ids = {item["source_record_id"] for item in report["sections"]["used"]}
    review_by_id = {item["source_record_id"]: item for item in report["sections"]["needs_review"]}
    assert procedure["id"] not in used_ids
    assert review_by_id[root["id"]]["reason_codes"] == ["lineage_status:degraded"]
    assert review_by_id[middle["id"]]["reason_codes"] == ["depends_on:ineligible"]
    assert review_by_id[middle["id"]]["blocked_by_id"] == root["id"]
    assert review_by_id[procedure["id"]]["reason_codes"] == ["depends_on:ineligible"]
    assert review_by_id[procedure["id"]]["blocked_by_id"] == middle["id"]


def test_dependency_classifier_ignores_descriptions_but_keeps_record_evidence(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    descriptive = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] descriptive runtime prerequisite",
        content=(
            "record_type: procedure\n"
            "goal: Run with a descriptive runtime prerequisite.\n"
            "steps: verify runtime | execute\n"
            "depends_on: python>=3.11\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:dependency-classifier"],
    )
    missing_id = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] missing approval record",
        content=(
            "record_type: procedure\n"
            "goal: Run with a missing approval record.\n"
            "steps: verify approval | execute\n"
            "depends_on: missing-release-approval\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:dependency-classifier"],
    )
    odd_id = "python>=3.12"
    _store_with_id(
        store,
        odd_id,
        namespace=NAMESPACE,
        kind="memory",
        title="Temporary runtime evidence",
        content="record_type: learn\nclaim: Runtime evidence existed.\n",
        tags=["kind:learn", "domain:release"],
    )
    store.forget(odd_id)
    tombstoned = store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="[[Procedure]] tombstoned runtime evidence",
        content=(
            "record_type: procedure\n"
            "goal: Run with tombstoned runtime evidence.\n"
            "steps: verify runtime evidence | execute\n"
            f"depends_on: {odd_id}\n"
        ),
        tags=["kind:procedure", "domain:release", "topic:dependency-classifier"],
    )

    report = build_task_brief_report(
        store,
        query="dependency classifier prerequisite approval evidence",
        namespace=NAMESPACE,
        generated_at=AS_OF,
        as_of=AS_OF,
        task_domain="release",
    )

    used_ids = {item["source_record_id"] for item in report["sections"]["used"]}
    review_ids = {item["source_record_id"] for item in report["sections"]["needs_review"]}
    ignored = {
        item["source_record_id"]: item
        for item in report["sections"]["ignored"]
        if "descriptive-dependency" in item["reason_codes"]
    }
    assert descriptive["id"] in used_ids
    assert descriptive["id"] in ignored
    assert ignored[descriptive["id"]]["dependency_value"] == "python>=3.11"
    assert missing_id["id"] not in used_ids
    assert "missing-release-approval" in review_ids
    assert tombstoned["id"] not in used_ids
    assert odd_id in review_ids
