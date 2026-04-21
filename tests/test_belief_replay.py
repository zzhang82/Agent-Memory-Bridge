from pathlib import Path

from agent_mem_bridge.belief_replay import (
    BeliefReplayConfig,
    diff_belief_replay_reports,
    run_belief_replay,
)
from agent_mem_bridge.storage import MemoryStore


def _store_source_row(
    store: MemoryStore,
    *,
    title: str,
    record_type: str,
    claim: str,
    domain: str,
    topic: str,
    session_id: str | None,
    correlation_id: str | None,
    trigger: str = "",
    symptom: str = "",
    fix: str = "",
    confidence: str = "observed",
    project: str = "project:mem-store",
    extra_tags: list[str] | None = None,
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
    lines.extend(["scope: global", f"confidence: {confidence}"])
    store.store(
        namespace="global",
        kind="memory",
        title=title,
        content="\n".join(lines),
        tags=[f"kind:{record_type}", domain, topic, project, *(extra_tags or [])],
        session_id=session_id,
        actor="bridge-reflex",
        correlation_id=correlation_id,
        source_app="agent-memory-bridge-reflex",
    )


def test_run_belief_replay_detects_candidate_belief_and_red_flag(tmp_path: Path) -> None:
    source_store = MemoryStore(tmp_path / "source.db", log_dir=tmp_path / "logs")
    _store_source_row(
        source_store,
        title="[[Learn]] bundle first",
        record_type="learn",
        claim="Load compact startup records before older operating manuals.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="r1",
        correlation_id="t1",
    )
    _store_source_row(
        source_store,
        title="[[Learn]] startup stays compact",
        record_type="learn",
        claim="Keep startup focused on core-policy, persona, soul, and project memory.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="r2",
        correlation_id="t2",
    )
    _store_source_row(
        source_store,
        title="[[Gotcha]] legacy docs outrank bundle",
        record_type="gotcha",
        claim="Old reference docs should not outrank the compact profile bundle.",
        trigger="Startup retrieval reaches for legacy docs before checking record-tagged bundle rows.",
        symptom="The agent leans on old structure even when the new bundle already covers the decision.",
        fix="Prefer record-tagged profile hits first, then use reference docs only as fallback.",
        confidence="validated",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="r3",
        correlation_id="t3",
    )
    _store_source_row(
        source_store,
        title="[[Gotcha]] startup blob hides new layer",
        record_type="gotcha",
        claim="Legacy startup blobs should not displace compact record-tagged guidance.",
        trigger="The system treats old operating docs as the first stop.",
        symptom="Bundle-first startup never gets a clean chance to carry the decision.",
        fix="Prefer record-tagged profile hits first, then use reference docs only as fallback.",
        confidence="validated",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="r4",
        correlation_id="t4",
    )
    _store_source_row(
        source_store,
        title="[[Learn]] fallback stays secondary",
        record_type="learn",
        claim="Keep old startup references as fallback instead of the default operating payload.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="r5",
        correlation_id="t5",
    )

    report = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=4,
            belief_to_domain_note_ratio_red_flag=0.5,
            stop_on_red_flag=True,
        ),
    )

    assert report["summary"]["source_row_count"] == 5
    assert report["summary"]["first_candidate_window"] == 1
    assert report["summary"]["first_belief_window"] == 2
    assert report["summary"]["first_red_flag_window"] == 2
    assert report["summary"]["stopped_on_red_flag"] is True
    assert report["windows"][0]["summary"]["belief_candidate_count"] == 1
    assert report["windows"][1]["summary"]["belief_count"] == 1
    assert len(report["windows"][1]["leaderboards"]["beliefs"]) == 1
    assert report["windows"][1]["leaderboards"]["beliefs"][0]["status"] == "belief"
    assert "fallback" in report["windows"][1]["leaderboards"]["beliefs"][0]["claim"]
    assert report["windows"][1]["leaderboards"]["candidates"][0]["status"] == "promoted"
    assert report["windows"][1]["cohorts"]["by_domain"][0]["domain"] == "domain:retrieval"
    assert "belief-to-domain-note-ratio" in report["windows"][1]["red_flags"]


def test_run_belief_replay_respects_domain_filters(tmp_path: Path) -> None:
    source_store = MemoryStore(tmp_path / "source.db", log_dir=tmp_path / "logs")
    _store_source_row(
        source_store,
        title="[[Learn]] retrieval one",
        record_type="learn",
        claim="Load compact startup records before older operating manuals.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="f1",
        correlation_id="ft1",
    )
    _store_source_row(
        source_store,
        title="[[Learn]] sqlite path",
        record_type="learn",
        claim="Keep one canonical runtime path for bridge state.",
        domain="domain:memory-bridge",
        topic="topic:runtime-path",
        session_id="f2",
        correlation_id="ft2",
    )

    report = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=2,
            domain_tags=("domain:retrieval",),
            stop_on_red_flag=False,
        ),
    )

    assert report["summary"]["source_row_count"] == 1
    assert report["summary"]["window_count"] == 1
    assert report["windows"][0]["summary"]["domain_note_count"] == 0
    assert report["windows"][0]["leaderboards"]["candidates"] == []


def test_run_belief_replay_same_session_burst_stays_candidate_only(tmp_path: Path) -> None:
    source_store = MemoryStore(tmp_path / "source.db", log_dir=tmp_path / "logs")
    for index, claim in enumerate(
        (
            "Keep startup compact and inspectable.",
            "Prefer compact startup over bloated startup docs.",
            "Treat old startup references as fallback, not default payload.",
            "Keep startup records explicit and small.",
            "Use compact startup records before older operating manuals.",
        ),
        start=1,
    ):
        _store_source_row(
            source_store,
            title=f"[[Learn]] burst {index}",
            record_type="learn",
            claim=claim,
            domain="domain:startup-protocol",
            topic="topic:startup-protocol",
            session_id="burst-session",
            correlation_id=f"burst-thread-{index}",
        )

    report = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=3,
            stop_on_red_flag=False,
        ),
    )

    final_window = report["windows"][-1]
    assert report["summary"]["first_candidate_window"] == 1
    assert report["summary"]["first_belief_window"] is None
    assert final_window["summary"]["belief_count"] == 0
    assert final_window["summary"]["belief_candidate_count"] >= 1
    assert any(row["status"] == "blocked-low-support" for row in final_window["leaderboards"]["candidates"])


def test_run_belief_replay_aging_blocks_promotion_from_stale_candidate_history(tmp_path: Path) -> None:
    source_store = MemoryStore(tmp_path / "source.db", log_dir=tmp_path / "logs")
    for index, claim in enumerate(
        (
            "Load compact startup records before older operating manuals.",
            "Keep startup focused on core-policy, persona, soul, and project memory.",
            "Old reference docs should not outrank the compact profile bundle.",
            "Legacy startup blobs should not displace compact record-tagged guidance.",
            "Keep old startup references as fallback instead of the default operating payload.",
        ),
        start=1,
    ):
        _store_source_row(
            source_store,
            title=f"[[Learn]] aging {index}",
            record_type="learn" if index in (1, 2, 5) else "gotcha",
            claim=claim,
            domain="domain:retrieval",
            topic="topic:startup-protocol",
            session_id=f"aging-session-{index}",
            correlation_id=f"aging-thread-{index}",
            confidence="validated" if index in (3, 4) else "observed",
            trigger="Startup retrieval reaches for legacy docs before checking record-tagged bundle rows." if index == 3 else (
                "The system treats old operating docs as the first stop." if index == 4 else ""
            ),
            symptom="The agent leans on old structure even when the new bundle already covers the decision." if index == 3 else (
                "Bundle-first startup never gets a clean chance to carry the decision." if index == 4 else ""
            ),
            fix="Prefer record-tagged profile hits first, then use reference docs only as fallback." if index in (3, 4) else "",
        )

    report = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=4,
            stop_on_red_flag=False,
            age_candidates_after_windows=(1,),
            age_candidates_by_days=30,
        ),
    )

    first_window = report["windows"][0]
    final_window = report["windows"][-1]
    assert "aged-candidates:30d" in first_window["actions_applied"]
    assert report["summary"]["first_candidate_window"] == 1
    assert report["summary"]["first_belief_window"] is None
    assert final_window["summary"]["belief_count"] == 0
    assert any(row["status"] == "blocked-stability" for row in final_window["leaderboards"]["candidates"])


def test_run_belief_replay_reports_out_of_filter_domains(tmp_path: Path) -> None:
    source_store = MemoryStore(tmp_path / "source.db", log_dir=tmp_path / "logs")
    _store_source_row(
        source_store,
        title="[[Learn]] retrieval startup",
        record_type="learn",
        claim="Prefer compact startup retrieval before legacy docs.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="spill-1",
        correlation_id="spill-thread-1",
        extra_tags=["domain:memory-bridge"],
    )
    _store_source_row(
        source_store,
        title="[[Gotcha]] retrieval runtime path",
        record_type="gotcha",
        claim="Shared runtime paths keep retrieval history visible.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="spill-2",
        correlation_id="spill-thread-2",
        trigger="Different runtime paths hide recent retrieval history.",
        symptom="Focused retrieval misses recent observations.",
        fix="Keep one runtime path while retrieval logic is evolving.",
        confidence="validated",
        extra_tags=["domain:memory-bridge"],
    )
    _store_source_row(
        source_store,
        title="[[Learn]] retrieval fallback",
        record_type="learn",
        claim="Use legacy retrieval docs only as fallback.",
        domain="domain:retrieval",
        topic="topic:startup-protocol",
        session_id="spill-3",
        correlation_id="spill-thread-3",
    )

    report = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=3,
            domain_tags=("domain:retrieval",),
            stop_on_red_flag=False,
        ),
    )

    assert report["summary"]["first_out_of_filter_window"] == 1
    assert "domain:memory-bridge" in report["windows"][0]["out_of_filter_domains"]


def test_run_belief_replay_same_session_multi_correlation_stays_candidate_only(tmp_path: Path) -> None:
    source_store = MemoryStore(tmp_path / "source.db", log_dir=tmp_path / "logs")
    for index, claim in enumerate(
        (
            "Keep startup compact and explicit.",
            "Prefer startup records over bloated startup manuals.",
            "Use older startup docs only as fallback.",
            "Keep startup guidance inspectable during migration.",
            "Let compact startup carry the default operating load.",
        ),
        start=1,
    ):
        _store_source_row(
            source_store,
            title=f"[[Learn]] corr burst {index}",
            record_type="learn",
            claim=claim,
            domain="domain:startup-protocol",
            topic="topic:startup-protocol",
            session_id="shared-session",
            correlation_id=f"corr-{index}",
        )

    report = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=5,
            session_ids=("shared-session",),
            stop_on_red_flag=False,
        ),
    )

    assert report["summary"]["source_row_count"] == 5
    assert report["summary"]["first_candidate_window"] == 1
    assert report["summary"]["first_belief_window"] is None
    final_window = report["windows"][-1]
    assert final_window["summary"]["belief_count"] == 0
    assert any(row["status"] == "blocked-low-support" for row in final_window["leaderboards"]["candidates"])


def test_run_belief_replay_session_and_correlation_filters_narrow_source_rows(tmp_path: Path) -> None:
    source_store = MemoryStore(tmp_path / "source.db", log_dir=tmp_path / "logs")
    _store_source_row(
        source_store,
        title="[[Learn]] target row",
        record_type="learn",
        claim="Keep replay filters inspectable.",
        domain="domain:memory-governance",
        topic="topic:belief-replay",
        session_id="target-session",
        correlation_id="target-correlation",
    )
    _store_source_row(
        source_store,
        title="[[Learn]] wrong session",
        record_type="learn",
        claim="This row should be filtered out by session.",
        domain="domain:memory-governance",
        topic="topic:belief-replay",
        session_id="other-session",
        correlation_id="target-correlation",
    )
    _store_source_row(
        source_store,
        title="[[Learn]] wrong correlation",
        record_type="learn",
        claim="This row should be filtered out by correlation.",
        domain="domain:memory-governance",
        topic="topic:belief-replay",
        session_id="target-session",
        correlation_id="other-correlation",
    )

    report = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=5,
            session_ids=("target-session",),
            correlation_ids=("target-correlation",),
            stop_on_red_flag=False,
        ),
    )

    assert report["summary"]["source_row_count"] == 1
    assert report["summary"]["window_count"] == 1


def test_run_belief_replay_session_null_only_filters_null_session_rows(tmp_path: Path) -> None:
    source_store = MemoryStore(tmp_path / "source.db", log_dir=tmp_path / "logs")
    _store_source_row(
        source_store,
        title="[[Learn]] null session row",
        record_type="learn",
        claim="Rows without session provenance should still be replayable for diagnostics.",
        domain="domain:memory-governance",
        topic="topic:belief-replay",
        session_id=None,
        correlation_id="null-correlation",
    )
    _store_source_row(
        source_store,
        title="[[Learn]] sessionful row",
        record_type="learn",
        claim="This row should be filtered out when null-only mode is enabled.",
        domain="domain:memory-governance",
        topic="topic:belief-replay",
        session_id="has-session",
        correlation_id="null-correlation",
    )

    report = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=5,
            session_null_only=True,
            stop_on_red_flag=False,
        ),
    )

    assert report["summary"]["source_row_count"] == 1
    assert report["summary"]["window_count"] == 1


def test_diff_belief_replay_reports_surfaces_variant_delay(tmp_path: Path) -> None:
    source_store = MemoryStore(tmp_path / "source.db", log_dir=tmp_path / "logs")
    for index, claim in enumerate(
        (
            "Load compact startup records before older operating manuals.",
            "Keep startup focused on core-policy, persona, soul, and project memory.",
            "Old reference docs should not outrank the compact profile bundle.",
            "Legacy startup blobs should not displace compact record-tagged guidance.",
            "Keep old startup references as fallback instead of the default operating payload.",
        ),
        start=1,
    ):
        _store_source_row(
            source_store,
            title=f"[[Learn]] diff {index}",
            record_type="learn" if index in (1, 2, 5) else "gotcha",
            claim=claim,
            domain="domain:retrieval",
            topic="topic:startup-protocol",
            session_id=f"diff-session-{index}",
            correlation_id=f"diff-thread-{index}",
            confidence="validated" if index in (3, 4) else "observed",
            trigger="Startup retrieval reaches for legacy docs before checking record-tagged bundle rows." if index == 3 else (
                "The system treats old operating docs as the first stop." if index == 4 else ""
            ),
            symptom="The agent leans on old structure even when the new bundle already covers the decision." if index == 3 else (
                "Bundle-first startup never gets a clean chance to carry the decision." if index == 4 else ""
            ),
            fix="Prefer record-tagged profile hits first, then use reference docs only as fallback." if index in (3, 4) else "",
        )

    baseline = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=4,
            stop_on_red_flag=False,
        ),
    )
    variant = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=4,
            stop_on_red_flag=False,
            age_candidates_after_windows=(1,),
            age_candidates_by_days=30,
        ),
    )
    diff = diff_belief_replay_reports(
        baseline_report=baseline,
        variant_report=variant,
    )

    assert diff["summary"]["baseline_first_belief_window"] == 2
    assert diff["summary"]["variant_first_belief_window"] is None
    assert diff["summary"]["belief_emergence"] == "suppressed-in-variant"
    assert diff["windows"][0]["variant"]["actions_applied"] == ["aged-candidates:30d"]


def test_run_belief_replay_null_session_uplift_by_day_can_unlock_belief(tmp_path: Path) -> None:
    source_store = MemoryStore(tmp_path / "source.db", log_dir=tmp_path / "logs")
    rows = [
        ("2026-04-01T12:00:00+00:00", "[[Learn]] uplift 1", "Load compact startup records before older operating manuals.", "observed", "", "", ""),
        ("2026-04-02T12:00:00+00:00", "[[Learn]] uplift 2", "Keep startup focused on core-policy, persona, soul, and project memory.", "observed", "", "", ""),
        ("2026-04-03T12:00:00+00:00", "[[Gotcha]] uplift 3", "Old reference docs should not outrank the compact profile bundle.", "validated", "Startup retrieval reaches for legacy docs before checking record-tagged bundle rows.", "The agent leans on old structure even when the new bundle already covers the decision.", "Prefer record-tagged profile hits first, then use reference docs only as fallback."),
        ("2026-04-04T12:00:00+00:00", "[[Gotcha]] uplift 4", "Legacy startup blobs should not displace compact record-tagged guidance.", "validated", "The system treats old operating docs as the first stop.", "Bundle-first startup never gets a clean chance to carry the decision.", "Prefer record-tagged profile hits first, then use reference docs only as fallback."),
        ("2026-04-05T12:00:00+00:00", "[[Learn]] uplift 5", "Keep old startup references as fallback instead of the default operating payload.", "observed", "", "", ""),
    ]
    for created_at, title, claim, confidence, trigger, symptom, fix in rows:
        _store_source_row(
            source_store,
            title=title,
            record_type="gotcha" if "Gotcha" in title else "learn",
            claim=claim,
            domain="domain:retrieval",
            topic="topic:startup-protocol",
            session_id=None,
            correlation_id=None,
            confidence=confidence,
            trigger=trigger,
            symptom=symptom,
            fix=fix,
        )
        with source_store._connect() as conn:
            conn.execute("UPDATE memories SET created_at = ? WHERE title = ?", (created_at, title))
            conn.commit()

    baseline = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=4,
            session_null_only=True,
            stop_on_red_flag=False,
        ),
    )
    uplifted = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=4,
            session_null_only=True,
            null_session_uplift_mode="by_day",
            stop_on_red_flag=False,
        ),
    )

    assert baseline["summary"]["first_belief_window"] is None
    assert uplifted["summary"]["first_belief_window"] == 2


def test_diff_belief_replay_reports_surfaces_uplifted_belief_emergence(tmp_path: Path) -> None:
    source_store = MemoryStore(tmp_path / "source.db", log_dir=tmp_path / "logs")
    for index, created_at in enumerate(
        (
            "2026-04-01T12:00:00+00:00",
            "2026-04-02T12:00:00+00:00",
            "2026-04-03T12:00:00+00:00",
            "2026-04-04T12:00:00+00:00",
            "2026-04-05T12:00:00+00:00",
        ),
        start=1,
    ):
        _store_source_row(
            source_store,
            title=f"[[Learn]] uplift diff {index}",
            record_type="learn" if index in (1, 2, 5) else "gotcha",
            claim=(
                "Load compact startup records before older operating manuals."
                if index == 1
                else "Keep startup focused on core-policy, persona, soul, and project memory."
                if index == 2
                else "Old reference docs should not outrank the compact profile bundle."
                if index == 3
                else "Legacy startup blobs should not displace compact record-tagged guidance."
                if index == 4
                else "Keep old startup references as fallback instead of the default operating payload."
            ),
            domain="domain:retrieval",
            topic="topic:startup-protocol",
            session_id=None,
            correlation_id=None,
            confidence="validated" if index in (3, 4) else "observed",
            trigger="Startup retrieval reaches for legacy docs before checking record-tagged bundle rows." if index == 3 else ("The system treats old operating docs as the first stop." if index == 4 else ""),
            symptom="The agent leans on old structure even when the new bundle already covers the decision." if index == 3 else ("Bundle-first startup never gets a clean chance to carry the decision." if index == 4 else ""),
            fix="Prefer record-tagged profile hits first, then use reference docs only as fallback." if index in (3, 4) else "",
        )
        with source_store._connect() as conn:
            conn.execute("UPDATE memories SET created_at = ? WHERE title = ?", (created_at, f"[[Learn]] uplift diff {index}"))
            conn.commit()

    baseline = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=4,
            session_null_only=True,
            stop_on_red_flag=False,
        ),
    )
    variant = run_belief_replay(
        source_store=source_store,
        config=BeliefReplayConfig(
            source_namespace="global",
            target_namespace="global",
            source_limit=20,
            window_size=4,
            session_null_only=True,
            null_session_uplift_mode="by_day",
            stop_on_red_flag=False,
        ),
    )
    diff = diff_belief_replay_reports(
        baseline_report=baseline,
        variant_report=variant,
    )

    assert diff["summary"]["baseline_first_belief_window"] is None
    assert diff["summary"]["variant_first_belief_window"] == 2
    assert diff["summary"]["belief_emergence"] == "new-in-variant"


def test_diff_belief_replay_reports_surfaces_contradiction_reason_deltas() -> None:
    baseline = {
        "summary": {
            "first_candidate_window": 1,
            "first_belief_window": None,
            "first_red_flag_window": None,
            "first_out_of_filter_window": None,
        },
        "windows": [
            {
                "summary": {
                    "belief_candidate_count": 1,
                    "belief_count": 0,
                    "domain_note_count": 1,
                    "blocked_by_contradiction": 1,
                    "blocked_by_staleness": 0,
                    "blocked_by_low_support": 0,
                    "blocked_by_stability": 0,
                    "contradiction_reason_counts": {
                        "marker-contrast": 2,
                        "no-marker": 5,
                    },
                },
                "red_flags": [],
                "actions_applied": [],
                "out_of_filter_domains": [],
            }
        ],
    }
    variant = {
        "summary": {
            "first_candidate_window": 1,
            "first_belief_window": None,
            "first_red_flag_window": None,
            "first_out_of_filter_window": None,
        },
        "windows": [
            {
                "summary": {
                    "belief_candidate_count": 1,
                    "belief_count": 0,
                    "domain_note_count": 1,
                    "blocked_by_contradiction": 1,
                    "blocked_by_staleness": 0,
                    "blocked_by_low_support": 0,
                    "blocked_by_stability": 0,
                    "contradiction_reason_counts": {
                        "marker-contrast": 1,
                        "no-marker": 6,
                        "boundary-exempt:manual-policy": 1,
                    },
                },
                "red_flags": [],
                "actions_applied": [],
                "out_of_filter_domains": [],
            }
        ],
    }

    diff = diff_belief_replay_reports(baseline_report=baseline, variant_report=variant)

    assert diff["windows"][0]["delta"]["contradiction_reason_counts"] == {
        "boundary-exempt:manual-policy": 1,
        "marker-contrast": -1,
        "no-marker": 1,
    }
