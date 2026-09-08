from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_mem_bridge.semantic_policy import (
    POLICY_SCHEMA,
    POLICY_VERSION,
    canonical_policy,
    canonical_policy_json,
    classify_task,
    host_adapter,
    policy_sha256,
    render_policy,
    supported_policy_hosts,
)


def test_canonical_policy_is_stable_and_bounded() -> None:
    policy = canonical_policy()

    assert policy["schema"] == POLICY_SCHEMA
    assert policy["version"] == POLICY_VERSION
    assert {rule["id"] for rule in policy["decision_rules"]["must_recall"]} >= {
        "material-prior-context",
        "fresh-session-or-handoff",
        "indirect-history-reference",
    }
    assert "not-for-tool-use" in {rule["id"] for rule in policy["decision_rules"]["do_not_recall"]}
    assert "automatic writeback" in " ".join(policy["boundaries"])
    assert policy_sha256() == policy_sha256()
    assert json.loads(canonical_policy_json()) == policy


def test_host_renderers_share_semantics_but_keep_placement_thin() -> None:
    rendered = {host: render_policy(host) for host in supported_policy_hosts()}
    json_views = {host: json.loads(render_policy(host, format="json").content) for host in rendered}

    assert set(rendered) == {"generic", "codex", "opencode"}
    assert {item.policy_sha256 for item in rendered.values()} == {policy_sha256()}
    assert all(item["policy"] == canonical_policy() for item in json_views.values())
    assert json_views["codex"]["adapter"]["instruction_surface"] == "project AGENTS.md or AGENTS.override.md"
    assert "manual" in json_views["generic"]["adapter"]["placement"].lower()
    assert "AGENTS.md" in rendered["opencode"].content
    assert "do not depend on literal words" in rendered["opencode"].content


def test_policy_rejects_unknown_host_and_format() -> None:
    with pytest.raises(ValueError, match="Unsupported policy host"):
        host_adapter("unknown")
    with pytest.raises(ValueError, match="format must be markdown or json"):
        render_policy("generic", format="text")


def test_canonical_policy_classifies_frozen_eval_cases_without_literal_keywords() -> None:
    fixture = json.loads(
        (Path(__file__).resolve().parents[1] / "benchmark" / "semantic-invocation-v1.json").read_text(encoding="utf-8")
    )
    observed = {}
    for case in fixture["cases"]:
        observed[case["id"]] = classify_task(
            case["prompt"],
            prior_recall_sufficient=case["id"] == "negative-recall-sufficient-no-repeat",
            amb_available=case["id"] != "governance-amb-unavailable",
            arm="C",
        )
        assert observed[case["id"]] == case["expected_decision"], case["id"]

    indirect = next(case for case in fixture["cases"] if case["id"] == "positive-indirect-history-reference")
    stale = next(case for case in fixture["cases"] if case["id"] == "governance-stale-superseded")
    typo = next(case for case in fixture["cases"] if case["id"] == "negative-isolated-typo-fix")
    assert classify_task(indirect["prompt"], arm="A") == "do_not_recall"
    assert classify_task(stale["prompt"], arm="B") == "must_recall"
    assert classify_task(typo["prompt"], arm="A") == "do_not_recall"
    assert classify_task(typo["prompt"], arm="B") == "do_not_recall"
