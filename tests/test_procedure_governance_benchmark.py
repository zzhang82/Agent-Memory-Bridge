from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_mem_bridge.procedure_governance_benchmark import (
    DEFAULT_CASES_PATH,
    evaluate_procedure_governance_packet,
    load_procedure_governance_cases,
    run_procedure_governance_benchmark,
)


def test_procedure_governance_benchmark_compares_flat_and_governed_packets(tmp_path: Path) -> None:
    cases_path = tmp_path / "procedure-cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "sample-governance-case",
                        "query": "release checklist",
                        "project_namespace": "project:demo",
                        "global_namespace": "global",
                        "expectations": {
                            "expected_top_procedure_title": "[[Procedure]] release checklist current",
                            "required_procedure_titles": ["[[Procedure]] release checklist current"],
                            "blocked_procedure_titles": ["[[Procedure]] release checklist stale"],
                            "expected_governance_status_by_title": {
                                "[[Procedure]] release checklist current": "validated"
                            },
                            "required_fields_by_title": {
                                "[[Procedure]] release checklist current": ["goal", "steps"]
                            },
                        },
                        "records": [
                            {
                                "local_id": "stale",
                                "namespace": "project:demo",
                                "title": "[[Procedure]] release checklist stale",
                                "content": (
                                    "record_type: procedure\n"
                                    "procedure_status: stale\n"
                                    "goal: stale\n"
                                    "steps: skip checks\n"
                                ),
                                "tags": ["kind:procedure"],
                            },
                            {
                                "local_id": "current",
                                "namespace": "project:demo",
                                "title": "[[Procedure]] release checklist current",
                                "content": (
                                    "record_type: procedure\n"
                                    "procedure_status: validated\n"
                                    "goal: current\n"
                                    "steps: run checks | tag release\n"
                                ),
                                "tags": ["kind:procedure"],
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls: list[bool] = []

    def fake_assembler(*args: Any, relation_aware: bool, **kwargs: Any) -> dict[str, Any]:
        calls.append(relation_aware)
        current = {
            "title": "[[Procedure]] release checklist current",
            "procedure": {
                "goal": "current",
                "steps": ["run checks", "tag release"],
                "governance": {"status": "validated", "warnings": []},
            },
        }
        stale = {
            "title": "[[Procedure]] release checklist stale",
            "procedure": {
                "goal": "stale",
                "steps": ["skip checks"],
                "governance": {"status": "stale", "warnings": ["ineligible-procedure-status:stale"]},
            },
        }
        if relation_aware:
            return {
                "procedure_hits": [current],
                "suppressed_items": [{"title": "[[Procedure]] release checklist stale"}],
            }
        return {
            "procedure_hits": [stale, current],
            "suppressed_items": [],
        }

    report = run_procedure_governance_benchmark(cases_path=cases_path, assembler=fake_assembler)

    assert calls == [False, True]
    assert report["summary"]["case_count"] == 1
    assert report["summary"]["flat_case_pass_rate"] == 0.0
    assert report["summary"]["governed_case_pass_rate"] == 1.0
    assert report["summary"]["flat_blocked_procedure_leak_rate"] == 1.0
    assert report["summary"]["governed_blocked_procedure_leak_rate"] == 0.0


def test_evaluate_procedure_governance_packet_checks_status_fields_and_warnings() -> None:
    case = {
        "expectations": {
            "expected_top_procedure_title": "Current",
            "required_procedure_titles": ["Current"],
            "blocked_procedure_titles": ["Old"],
            "expected_governance_status_by_title": {"Current": "draft"},
            "required_fields_by_title": {"Current": ["goal", "steps"]},
            "required_warnings_by_title": {"Current": ["draft-procedure"]},
        }
    }
    packet = {
        "procedure_hits": [
            {
                "title": "Current",
                "procedure": {
                    "goal": "Do the thing.",
                    "steps": ["step one"],
                    "governance": {"status": "draft", "warnings": ["draft-procedure"]},
                },
            }
        ],
        "suppressed_items": [{"title": "Old"}],
    }

    score = evaluate_procedure_governance_packet(packet, case)

    assert score["case_passed"] is True
    assert score["governance_statuses"]["hit_count"] == 1
    assert score["required_fields"]["hit_count"] == 2
    assert score["required_warnings"]["hit_count"] == 1
    assert score["blocked_procedures"]["leaked"] == []


def test_bundled_procedure_governance_cases_are_reviewed_and_loadable() -> None:
    cases = load_procedure_governance_cases(DEFAULT_CASES_PATH)

    assert len(cases) >= 6
    assert {case["id"] for case in cases} >= {
        "validated-beats-draft",
        "stale-status-suppression",
        "replaced-status-with-supersedes",
        "complete-fields-surface",
        "legacy-unspecified-status-compatible",
        "transcript-like-procedure-warning",
    }
    for case in cases:
        expectations = case["expectations"]
        assert case["query"].strip()
        assert expectations["required_procedure_titles"]
        assert "blocked_procedure_titles" in expectations


def test_bundled_procedure_governance_benchmark_shows_local_governance_improvement() -> None:
    report = run_procedure_governance_benchmark()
    summary = report["summary"]

    assert summary["case_count"] >= 6
    assert summary["governed_case_pass_rate"] > summary["flat_case_pass_rate"]
    assert summary["governed_case_pass_rate"] == 1.0
    assert summary["governed_top_procedure_match_rate"] == 1.0
    assert summary["governed_blocked_procedure_leak_rate"] == 0.0
    assert summary["governed_required_field_hit_rate"] == 1.0
    assert "does not claim productivity gains" in report["metadata"]["notes"]
