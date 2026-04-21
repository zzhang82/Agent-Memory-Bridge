import json
from pathlib import Path

from agent_mem_bridge.archive_snapshot import write_live_source_manifest
from agent_mem_bridge.cutover_dashboard import (
    CutoverDashboardConfig,
    StartupCase,
    build_cutover_dashboard,
    load_startup_cases,
    render_cutover_dashboard_text,
)
from agent_mem_bridge.live_cutover import apply_live_source_cutover, build_default_cutover_root
from agent_mem_bridge.profile_migration import import_profile_memory
from agent_mem_bridge.storage import MemoryStore


def test_load_startup_cases_accepts_object_wrapper(tmp_path: Path) -> None:
    case_path = tmp_path / "startup-cases.json"
    case_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "rules",
                        "query": "verify before done",
                        "required_bundle_labels": ["core-policy"],
                        "max_reference_hits": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    cases = load_startup_cases(case_path)

    assert len(cases) == 1
    assert cases[0].case_id == "rules"
    assert cases[0].required_bundle_labels == ("core-policy",)


def test_load_startup_cases_accepts_utf8_bom(tmp_path: Path) -> None:
    case_path = tmp_path / "startup-cases-bom.json"
    payload = json.dumps(
        [
            {
                "id": "rules",
                "query": "verify before done",
                "required_bundle_labels": ["core-policy"],
            }
        ]
    )
    case_path.write_text(payload, encoding="utf-8-sig")

    cases = load_startup_cases(case_path)

    assert len(cases) == 1
    assert cases[0].case_id == "rules"


def test_cutover_dashboard_passes_with_clean_structure_and_startup_cases(tmp_path: Path) -> None:
    source_root = _build_profile_source(tmp_path)
    write_live_source_manifest(source_root, source_root / "live-source-manifest.json")
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    import_profile_memory(store, source_root)
    _store_profile_bundle(store, namespace="cole-core")
    store.store(
        namespace="project:alpha",
        kind="memory",
        title="[[Project]] default workflow",
        content="claim: recurring workflow should stay explicit in project memory",
        tags=["project:alpha", "kind:summary"],
    )
    store.store(
        namespace="cole-core",
        kind="memory",
        title="[[Domain Note]] startup authority",
        content="record_type: domain-note\ndomain: domain:startup\nclaim: startup should load authority before narrative",
        tags=["kind:domain-note", "domain:startup"],
        actor="bridge-consolidation",
    )

    report = build_cutover_dashboard(
        store,
        CutoverDashboardConfig(
            source_root=source_root,
            global_namespace="cole-core",
            project_namespace="project:alpha",
            startup_cases=(
                StartupCase(
                    case_id="authority",
                    query="verify behavior before done",
                    required_bundle_labels=("core-policy",),
                    max_reference_hits=0,
                ),
                StartupCase(
                    case_id="project-default",
                    query="recurring workflow should stay explicit in project memory",
                    require_project_hit=True,
                    min_new_non_reference_hits=1,
                ),
            ),
        ),
    )

    assert report["overall"]["status"] == "go"
    assert "startup:all-cases-pass" in report["overall"]["reason_codes"]
    assert "belief:ratio-ok" in report["overall"]["reason_codes"]
    assert "rollback:pre-cutover" in report["overall"]["reason_codes"]
    assert report["structure"]["status"] == "pass"
    assert report["startup"]["status"] == "pass"
    assert report["belief"]["status"] == "pass"
    assert report["rollback"]["status"] == "pass"
    assert report["startup"]["summary"]["authority_pass_count"] == 2
    assert report["startup"]["summary"]["fallback_needed_case_count"] == 0

    rendered = render_cutover_dashboard_text(report)
    assert "overall_status: go" in rendered
    assert "overall_reason_codes:" in rendered
    assert "Structure [pass]" in rendered
    assert "Startup [pass]" in rendered


def test_cutover_dashboard_fails_when_required_profile_record_is_missing(tmp_path: Path) -> None:
    source_root = _build_profile_source(tmp_path)
    write_live_source_manifest(source_root, source_root / "live-source-manifest.json")
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    import_profile_memory(store, source_root)
    store.store(
        namespace="cole-core",
        kind="memory",
        title="[[Policy]] verify before done",
        content="claim: verify behavior before calling work done",
        tags=["record:core-policy", "control:policy"],
    )
    store.store(
        namespace="cole-core",
        kind="memory",
        title="[[Soul]] continuity matters",
        content="claim: build cumulative context across sessions",
        tags=["record:soul", "control:policy"],
    )

    report = build_cutover_dashboard(
        store,
        CutoverDashboardConfig(
            source_root=source_root,
            global_namespace="cole-core",
        ),
    )

    assert report["structure"]["status"] == "fail"
    assert report["structure"]["required_profile_record_counts"]["persona"] == 0
    assert report["overall"]["status"] == "no-go"
    assert "structure:missing-profile-records" in report["overall"]["reason_codes"]


def test_cutover_dashboard_fails_when_rollback_has_newer_live_conflict(tmp_path: Path) -> None:
    source_root = _build_profile_source(tmp_path)
    write_live_source_manifest(source_root, source_root / "live-source-manifest.json")
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    import_profile_memory(store, source_root)
    _store_profile_bundle(store, namespace="cole-core")

    cutover_result = apply_live_source_cutover(
        source_root,
        build_default_cutover_root(source_root),
        preflight_report={"missing_count": 0, "content_mismatch_count": 0, "namespace_mismatch_count": 0},
    )
    revived_file = source_root / "architecture.md"
    revived_file.write_text("# Architecture changed live\n", encoding="utf-8")

    report = build_cutover_dashboard(
        store,
        CutoverDashboardConfig(
            source_root=source_root,
            global_namespace="cole-core",
            cutover_manifest_path=Path(cutover_result["manifest_path"]),
        ),
    )

    assert report["rollback"]["status"] == "fail"
    assert report["rollback"]["preflight"]["newer_live_conflict_count"] == 1
    assert report["overall"]["status"] == "no-go"
    assert "rollback:newer-live-conflicts" in report["overall"]["reason_codes"]


def test_cutover_dashboard_holds_without_startup_cases(tmp_path: Path) -> None:
    source_root = _build_profile_source(tmp_path)
    write_live_source_manifest(source_root, source_root / "live-source-manifest.json")
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    import_profile_memory(store, source_root)
    _store_profile_bundle(store, namespace="cole-core")

    report = build_cutover_dashboard(
        store,
        CutoverDashboardConfig(
            source_root=source_root,
            global_namespace="cole-core",
        ),
    )

    assert report["startup"]["status"] == "pending"
    assert report["overall"]["status"] == "hold"
    assert "startup:pending-cases" in report["overall"]["reason_codes"]


def test_cutover_dashboard_warns_when_startup_still_depends_on_reference(tmp_path: Path) -> None:
    source_root = _build_profile_source(tmp_path)
    write_live_source_manifest(source_root, source_root / "live-source-manifest.json")
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    import_profile_memory(store, source_root)
    _store_profile_bundle(store, namespace="cole-core")
    for index in range(5):
        store.store(
            namespace="cole-core",
            kind="memory",
            title=f"[[Reference]] legacy startup manual {index}",
            content=f"claim: legacy startup manual still explains niche fallback path {index}",
            tags=["kind:reference", "record:legacy-doc"],
        )

    report = build_cutover_dashboard(
        store,
        CutoverDashboardConfig(
            source_root=source_root,
            global_namespace="cole-core",
            startup_cases=(
                StartupCase(
                    case_id="legacy-only-1",
                    query="legacy startup manual",
                    min_new_non_reference_hits=0,
                    max_reference_hits=5,
                ),
                StartupCase(
                    case_id="legacy-only-2",
                    query="legacy startup manual",
                    min_new_non_reference_hits=0,
                    max_reference_hits=5,
                ),
            ),
        ),
    )

    assert report["startup"]["status"] == "pass"
    assert "startup:fallback-free" in report["startup"]["reason_codes"]
    assert report["startup"]["summary"]["query_bundle_signal_case_count"] == 0
    assert report["startup"]["summary"]["fallback_only_bundle_case_count"] == 2
    assert report["startup"]["summary"]["legacy_reference_signal_case_count"] == 2
    assert report["startup"]["summary"]["legacy_total_reference_hits"] >= 2
    assert "startup:reference-dependency-high" in report["startup"]["warning_codes"]
    assert "startup:reference-hit-ratio-high" in report["startup"]["warning_codes"]
    assert "startup:bundle-fallback-only-high" in report["startup"]["warning_codes"]
    assert "startup:reference-dependency-high" in report["overall"]["warning_codes"]


def test_cutover_dashboard_warns_when_bundle_cases_only_pass_via_startup_fallback(tmp_path: Path) -> None:
    source_root = _build_profile_source(tmp_path)
    write_live_source_manifest(source_root, source_root / "live-source-manifest.json")
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    import_profile_memory(store, source_root)
    _store_profile_bundle(store, namespace="shadow:bundle")

    report = build_cutover_dashboard(
        store,
        CutoverDashboardConfig(
            source_root=source_root,
            global_namespace="shadow:bundle",
            startup_cases=(
                StartupCase(
                    case_id="fallback-only",
                    query="zqxjv norafield plumkite",
                    required_bundle_labels=("core-policy",),
                    min_new_non_reference_hits=0,
                    max_reference_hits=0,
                ),
            ),
        ),
    )

    assert report["startup"]["status"] == "pass"
    assert report["startup"]["summary"]["bundle_signal_case_count"] == 1
    assert report["startup"]["summary"]["query_bundle_signal_case_count"] == 0
    assert report["startup"]["summary"]["fallback_only_bundle_case_count"] == 1
    assert "startup:bundle-fallback-only-high" in report["startup"]["warning_codes"]
    assert report["startup"]["cases"][0]["fallback_only_bundle"] is True
    assert report["startup"]["cases"][0]["query_bundle_hit_labels"] == []
    assert report["startup"]["cases"][0]["startup_loaded_bundle_labels"] == ["core-policy", "persona", "soul"]


def _build_profile_source(tmp_path: Path) -> Path:
    root = tmp_path / "Cole"
    (root / "memory" / "core").mkdir(parents=True)
    (root / "memory").mkdir(exist_ok=True)

    (root / "HOW-TO-USE-COLE.md").write_text("# How to Use Cole\n\nLoad the bridge first.\n", encoding="utf-8")
    (root / "architecture.md").write_text("# Architecture\n\nverify behavior before done\n", encoding="utf-8")
    (root / "memory" / ".claude-memory-guard.md").write_text("# Guard\n", encoding="utf-8")
    (root / "memory" / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (root / "memory" / "QUEUE.md").write_text("# Queue\n", encoding="utf-8")
    (root / "memory" / "REDLINE.md").write_text("# Redline\n", encoding="utf-8")
    (root / "memory" / "core" / "core.md").write_text("# Core\n\nverify behavior before done\n", encoding="utf-8")
    (root / "memory" / "core" / "persona.md").write_text("# Persona\n\nCalm and direct.\n", encoding="utf-8")
    (root / "memory" / "core" / "decision-making.md").write_text("# Decisions\n\nPrefer explicit trade-offs.\n", encoding="utf-8")
    return root


def _store_profile_bundle(store: MemoryStore, *, namespace: str) -> None:
    store.store(
        namespace=namespace,
        kind="memory",
        title="[[Policy]] verify before done",
        content="claim: verify behavior before calling work done",
        tags=["record:core-policy", "control:policy"],
    )
    store.store(
        namespace=namespace,
        kind="memory",
        title="[[Persona]] calm and direct",
        content="claim: collaborate calmly and explain the why",
        tags=["record:persona", "control:policy"],
    )
    store.store(
        namespace=namespace,
        kind="memory",
        title="[[Soul]] continuity matters",
        content="claim: build cumulative context across sessions",
        tags=["record:soul", "control:policy"],
    )
