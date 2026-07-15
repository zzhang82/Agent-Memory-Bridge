from __future__ import annotations

from agent_mem_bridge.lineage import LineageRelation, extract_lineage_references, parse_lineage


def test_parse_lineage_reads_only_supported_structured_id_fields() -> None:
    lineage = parse_lineage(
        "\n".join(
            [
                "record_type: learning-review",
                "derived_from_candidate_id: candidate-1",
                "derived_from_belief_id: belief-1",
                "evidence_refs: source-1 | source-2 | source-1",
                'evidence_refs_json: ["source-2", "source-3", 4, null]',
                "source_candidate_id: staged-1",
                "candidate_id: staged-2",
                "target_record_id: durable-1",
                "supports: support-1 | support-2",
                'supports_record_ids_json: ["support-2", "support-3"]',
                "contradicts: conflict-1",
                'contradicts_record_ids_json: ["conflict-2"]',
                "supersedes: old-1",
                'supersedes_record_ids_json: ["old-2"]',
                "depends_on: dependency-1",
                'depends_on_record_ids_json: ["dependency-2"]',
            ]
        )
    )

    assert lineage.derived_from_candidate_id == "candidate-1"
    assert lineage.derived_from_belief_id == "belief-1"
    assert lineage.evidence_refs == ("source-1", "source-2", "source-3")
    assert lineage.source_candidate_id == "staged-1"
    assert lineage.candidate_id == "staged-2"
    assert lineage.target_record_id == "durable-1"
    assert lineage.supports == ("support-1", "support-2", "support-3")
    assert lineage.contradicts == ("conflict-1", "conflict-2")
    assert lineage.supersedes == ("old-1", "old-2")
    assert lineage.depends_on == ("dependency-1", "dependency-2")
    assert lineage.references_to("candidate-1")[0].relation is LineageRelation.DERIVED_FROM_CANDIDATE
    assert {reference.relation for reference in lineage.historical_references} == {
        LineageRelation.CONTRADICTS,
        LineageRelation.SUPERSEDES,
    }
    assert LineageRelation.SUPPORTS in {reference.relation for reference in lineage.degrading_references}
    assert LineageRelation.DEPENDS_ON in {reference.relation for reference in lineage.degrading_references}
    assert not {
        LineageRelation.CONTRADICTS,
        LineageRelation.SUPERSEDES,
    }.intersection(reference.relation for reference in lineage.degrading_references)


def test_parse_lineage_does_not_infer_ids_from_prose_or_malformed_typed_fields() -> None:
    content = "\n".join(
        [
            "claim: candidate_id: candidate-from-prose depends on missing-1",
            "notes: derived_from_candidate_id: embedded-id",
            "derived-from-belief-id: wrong-key-style",
            'evidence_refs_json: {"id": "not-a-list"}',
            'supports_record_ids_json: ["exact-support"] trailing text',
            "unrelated_record_ids_json: [\"unrelated-1\"]",
        ]
    )

    lineage = parse_lineage(content)

    assert lineage.target_ids == ()
    assert extract_lineage_references(content) == ()


def test_parse_lineage_preserves_exact_structured_id_values() -> None:
    lineage = parse_lineage(
        "candidate_id: Case-Sensitive_ID.01\n"
        'depends_on_record_ids_json: ["Case-Sensitive_ID.01", "other/id"]\n'
    )

    assert lineage.candidate_id == "Case-Sensitive_ID.01"
    assert lineage.depends_on == ("Case-Sensitive_ID.01", "other/id")
    assert lineage.target_ids == ("Case-Sensitive_ID.01", "other/id")
