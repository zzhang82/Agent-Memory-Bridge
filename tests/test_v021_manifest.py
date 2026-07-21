from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "benchmark" / "v0.21-governed-change-manifest.json"

EXPECTED_IDS = [
    "gmuc-del-01-forget-clears-primary-fts-embedding",
    "gmuc-del-02-tombstone-redacts-forgotten-content",
    "gmuc-del-03-tombstone-prevents-predecessor-resurrection",
    "gmuc-del-04-deleted-dependency-demotes-procedure",
    "gmuc-del-05-rebuild-cannot-resurrect-forgotten-record",
    "gmuc-life-01-stale-decoy-added-after-current",
    "gmuc-life-02-superseder-arrives-after-old",
    "gmuc-life-03-three-generation-supersession-chain",
    "gmuc-life-04-validity-crosses-expiry-boundary",
    "gmuc-life-05-expired-record-replaced-without-leak",
    "gmuc-state-01-rollout-completed-resists-continue",
    "gmuc-state-02-incident-closed-resists-escalate",
    "gmuc-state-03-migration-applied-resists-rerun",
    "gmuc-state-04-feature-removed-resists-enable",
    "gmuc-state-05-approval-revoked-resists-shortcut",
    "gmuc-xfer-01-sqlite-rejects-postgres-backup",
    "gmuc-xfer-02-python-release-rejects-k8s-rollback",
    "gmuc-xfer-03-signing-key-rejects-cloud-api-rotation",
    "gmuc-xfer-04-obsidian-vault-rejects-sql-schema-migration",
    "gmuc-xfer-05-skill-sync-rejects-deployment-sync",
]

EXPECTED_CATEGORIES = {
    "deletion_residue": 5,
    "lifecycle_supersession": 5,
    "changed_premise_usefulness": 5,
    "cross_domain_transfer": 5,
}

REQUIRED_CASE_FIELDS = {
    "id",
    "category",
    "transition",
    "expected_governed_outcome",
    "flat_baseline_hazard",
    "failure_reason",
    "metric_mappings",
    "no_go_guard",
    "checkpoints",
}


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_v021_manifest_has_exact_fixed_case_contract() -> None:
    manifest = load_manifest()
    cases = manifest["cases"]
    ids = [case["id"] for case in cases]

    assert manifest["schema"] == "amb.v0.21.governed_change_manifest.v1"
    assert manifest["title"] == "Governed Memory Under Change"
    assert manifest["status"] == "planning/pre-v0.21"
    assert manifest["case_count"] == 20
    assert len(cases) == 20
    assert ids == EXPECTED_IDS
    assert len(ids) == len(set(ids))
    assert Counter(case["category"] for case in cases) == EXPECTED_CATEGORIES
    assert manifest["categories"] == EXPECTED_CATEGORIES


def test_v021_cases_have_required_fields_metrics_and_two_checkpoints() -> None:
    manifest = load_manifest()
    checkpoint_ids: list[str] = []

    assert set(manifest["required_case_fields"]) == REQUIRED_CASE_FIELDS
    for case in manifest["cases"]:
        assert set(case) == REQUIRED_CASE_FIELDS
        assert case["transition"]["kind"] in {"storage_mutation", "clock_transition"}
        assert case["transition"]["description"]
        assert set(case["flat_baseline_hazard"]) == {"expected", "description"}
        assert isinstance(case["flat_baseline_hazard"]["expected"], bool)
        assert case["flat_baseline_hazard"]["description"]
        assert case["expected_governed_outcome"]
        assert case["failure_reason"]
        assert case["no_go_guard"]
        assert case["metric_mappings"] == {
            "flat_baseline": "flat_baseline_hazards",
            "governed_outcome": "governed_failures",
            "checkpoints": "governed_checkpoint_passes",
        }
        assert len(case["checkpoints"]) == 2
        for checkpoint in case["checkpoints"]:
            assert set(checkpoint) == {"id", "assertion"}
            assert checkpoint["assertion"]
            checkpoint_ids.append(checkpoint["id"])

    assert len(checkpoint_ids) == 40
    assert len(checkpoint_ids) == len(set(checkpoint_ids))


def test_v021_transition_and_metric_denominators_are_locked() -> None:
    manifest = load_manifest()
    cases = manifest["cases"]
    transition_counts = Counter(case["transition"]["kind"] for case in cases)
    contract = manifest["execution_contract"]

    assert contract == {
        "memory_store_isolation": "one_fresh_temp_MemoryStore_per_case",
        "checkpoints_per_case": 2,
        "transition_count": 20,
        "storage_mutation_count": 19,
        "clock_transition_count": 1,
    }
    assert transition_counts == {"storage_mutation": 19, "clock_transition": 1}
    assert sum(case["flat_baseline_hazard"]["expected"] for case in cases) == 17
    assert manifest["metrics"] == {
        "flat_baseline_hazards": {"expected": "17/20", "hazard_count": 17},
        "governed_failures": {"target": "0/20", "failure_count": 0},
        "governed_checkpoint_passes": {"target": "40/40", "pass_count": 40},
    }


def test_v021_is_pre_release_and_cannot_expand_or_mutate_product_surface() -> None:
    manifest = load_manifest()
    boundaries = manifest["boundaries"]

    assert manifest["current_release"] == "0.20.0"
    assert manifest["target_release"] == "0.21.0"
    assert boundaries["public_mcp_tool_count"] == 10
    assert boundaries["no_new_mcp_tools"] is True
    assert boundaries["no_auto_mutation"] is True
    assert boundaries["no_auto_writeback"] is True
    assert boundaries["no_config_writes"] is True
    assert boundaries["no_outside_temp_writes"] is True
    assert boundaries["no_private_or_local_cole_data"] is True
