from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_mem_bridge.task_memory_benchmark import (
    DEFAULT_CASES_PATH,
    evaluate_task_memory_packet,
    load_task_memory_cases,
    run_task_memory_benchmark,
)


def test_task_memory_benchmark_compares_flat_and_relation_aware_packets(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "sample-case",
                        "query": "release cutover",
                        "project_namespace": "project:demo",
                        "global_namespace": "global",
                        "expectations": {
                            "expected_top_primary_title": "[[Procedure]] release cutover current",
                            "required_primary_titles": ["[[Procedure]] release cutover current"],
                            "required_support_titles": ["[[Concept Note]] release cutover support"],
                            "blocked_titles": ["[[Procedure]] release cutover legacy"],
                        },
                        "records": [
                            {
                                "local_id": "support",
                                "namespace": "global",
                                "title": "[[Concept Note]] release cutover support",
                                "content": "record_type: concept-note\nclaim: support\n",
                                "tags": ["kind:concept-note"],
                            },
                            {
                                "local_id": "legacy",
                                "namespace": "project:demo",
                                "title": "[[Procedure]] release cutover legacy",
                                "content": "record_type: procedure\ngoal: legacy\n",
                                "tags": ["kind:procedure"],
                            },
                            {
                                "local_id": "current",
                                "namespace": "project:demo",
                                "title": "[[Procedure]] release cutover current",
                                "content": "record_type: procedure\ngoal: current\ndepends_on: {{support}}\nsupersedes: {{legacy}}\n",
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
        if relation_aware:
            return {
                "procedure_hits": [{"title": "[[Procedure]] release cutover current"}],
                "concept_hits": [],
                "belief_hits": [],
                "domain_hits": [],
                "supporting_hits": [{"title": "[[Concept Note]] release cutover support"}],
                "suppressed_items": [{"title": "[[Procedure]] release cutover legacy"}],
            }
        return {
            "procedure_hits": [
                {"title": "[[Procedure]] release cutover legacy"},
                {"title": "[[Procedure]] release cutover current"},
            ],
            "concept_hits": [],
            "belief_hits": [],
            "domain_hits": [],
            "supporting_hits": [],
            "suppressed_items": [],
        }

    report = run_task_memory_benchmark(cases_path=cases_path, assembler=fake_assembler)

    assert calls == [False, True]
    assert report["summary"]["case_count"] == 1
    assert report["summary"]["flat_case_pass_rate"] == 0.0
    assert report["summary"]["relation_aware_case_pass_rate"] == 1.0
    assert report["summary"]["flat_blocked_item_leak_rate"] == 1.0
    assert report["summary"]["relation_aware_blocked_item_leak_rate"] == 0.0
    assert report["summary"]["flat_required_support_hit_rate"] == 0.0
    assert report["summary"]["relation_aware_required_support_hit_rate"] == 1.0
    assert report["metadata"]["relation_aware_supported"] is True
    assert report["results"][0]["seeded_records"] == {
        "count": 3,
        "local_ids": ["current", "legacy", "support"],
    }


def test_evaluate_task_memory_packet_does_not_count_suppressed_items_as_leaks() -> None:
    case = {
        "expectations": {
            "expected_top_primary_title": "Current",
            "required_primary_titles": ["Current"],
            "required_support_titles": ["Support"],
            "blocked_titles": ["Old"],
        }
    }
    packet = {
        "procedure_hits": [{"title": "Current"}],
        "concept_hits": [],
        "belief_hits": [],
        "domain_hits": [],
        "supporting_hits": [{"title": "Support"}],
        "suppressed_items": [{"title": "Old"}],
    }

    score = evaluate_task_memory_packet(packet, case)

    assert score["case_passed"] is True
    assert score["blocked_items"]["leaked"] == []
    assert score["blocked_items"]["suppressed"] == ["Old"]
    assert score["packet_size"] == 2


def test_bundled_task_memory_cases_are_reviewed_and_loadable() -> None:
    cases = load_task_memory_cases(DEFAULT_CASES_PATH)

    assert len(cases) >= 4
    assert {case["id"] for case in cases} >= {
        "support-chain-completion",
        "superseded-procedure-suppression",
        "contradiction-leakage-control",
        "validity-window-filtering",
    }
    for case in cases:
        expectations = case["expectations"]
        assert case["query"].strip()
        assert expectations["required_primary_titles"]
        assert "blocked_titles" in expectations
