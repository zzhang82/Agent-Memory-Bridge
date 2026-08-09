from __future__ import annotations

import binascii
import json
import subprocess
import sys
import textwrap
import zlib
from pathlib import Path

import pytest

from agent_mem_bridge.release_contract import (
    V027_PUBLIC_TOOL_ORDER,
    build_v027_episode_release_check,
    collect_test_count,
    load_server_tool_names,
    run_release_contract_check,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_load_server_tool_names_supports_factory_registration_manifest(tmp_path: Path) -> None:
    server_path = tmp_path / "server.py"
    server_path.write_text(
        textwrap.dedent(
            """
            def store():
                pass

            def recall():
                pass

            _PUBLIC_TOOL_HANDLERS = (store, recall)
            """
        ),
        encoding="utf-8",
    )

    assert load_server_tool_names(server_path) == {"store", "recall"}


def test_load_server_tool_names_rejects_malformed_factory_manifest(tmp_path: Path) -> None:
    server_path = tmp_path / "server.py"
    server_path.write_text("def store():\n    pass\n\n_PUBLIC_TOOL_HANDLERS = (store, missing)\n", encoding="utf-8")

    with pytest.raises(ValueError, match="only defined function names"):
        load_server_tool_names(server_path)


def test_run_release_contract_check_passes_for_aligned_fixture(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)

    # 146 is the fixture-internal count declared in create_release_fixture below.
    # It is intentionally not the live test count. The fixture is a synthetic
    # tree where the README, report JSON, and test_count_provider are all set to the
    # same fixed value so the contract check passes deterministically without running
    # pytest on the real suite.
    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    assert report["ok"] is True
    assert report["pyproject_version"] == "0.9.0"
    assert report["server_tool_count"] == 13
    assert report["test_count_source"] == "pytest_collect_only"
    assert all(check["ok"] for check in report["checks"])

    v021_root = create_v021_release_fixture(tmp_path / "v021", package_version="0.21.2")
    v021_report = run_release_contract_check(v021_root, test_count_provider=lambda _: 146)
    checks = {check["name"]: check for check in v021_report["checks"]}
    assert v021_report["ok"] is True
    assert "v020_proof_version_matches_pyproject" not in checks
    proof_check = checks["v021_governed_change_proof_matches_release_gate"]
    assert proof_check["ok"] is True
    assert proof_check["package_version"] == "0.21.2"
    assert proof_check["actual_release"] == "0.21.0"
    assert proof_check["actual_target_release"] == "0.21.0"
    historical_v020 = json.loads(
        (v021_root / "benchmark" / "latest-v0.20-clean-room-proof-report.json").read_text(encoding="utf-8")
    )
    assert historical_v020["release"] == "0.9.0"

    v022_root = create_v021_release_fixture(tmp_path / "v022", package_version="0.22.0")
    v022_report = run_release_contract_check(v022_root, test_count_provider=lambda _: 146)
    v022_checks = {check["name"]: check for check in v022_report["checks"]}
    assert v022_report["ok"] is True
    assert "v020_proof_version_matches_pyproject" not in v022_checks
    v022_proof_check = v022_checks["v021_governed_change_proof_matches_release_gate"]
    assert v022_proof_check["ok"] is True
    assert v022_proof_check["package_version"] == "0.22.0"
    assert v022_proof_check["actual_release"] == "0.21.0"
    assert v022_proof_check["actual_target_release"] == "0.21.0"
    v022_historical_v020 = json.loads(
        (v022_root / "benchmark" / "latest-v0.20-clean-room-proof-report.json").read_text(encoding="utf-8")
    )
    assert v022_historical_v020["release"] == "0.9.0"

    v023_root = create_v021_release_fixture(tmp_path / "v023", package_version="0.23.0")
    v023_report = run_release_contract_check(v023_root, test_count_provider=lambda _: 146)
    v023_checks = {check["name"]: check for check in v023_report["checks"]}
    assert v023_report["ok"] is True
    assert "v020_proof_version_matches_pyproject" not in v023_checks
    v023_proof_check = v023_checks["v021_governed_change_proof_matches_release_gate"]
    assert v023_proof_check["ok"] is True
    assert v023_proof_check["package_version"] == "0.23.0"
    assert v023_proof_check["actual_release"] == "0.21.0"
    assert v023_proof_check["actual_target_release"] == "0.21.0"

    v025_root = create_v021_release_fixture(tmp_path / "v025", package_version="0.25.0")
    v025_report = run_release_contract_check(v025_root, test_count_provider=lambda _: 146)
    v025_checks = {check["name"]: check for check in v025_report["checks"]}
    assert v025_report["ok"] is True
    assert "v020_proof_version_matches_pyproject" not in v025_checks
    v025_proof_check = v025_checks["v021_governed_change_proof_matches_release_gate"]
    assert v025_proof_check["ok"] is True
    assert v025_proof_check["package_version"] == "0.25.0"
    assert v025_proof_check["actual_release"] == "0.21.0"
    assert v025_proof_check["actual_target_release"] == "0.21.0"

    v026_root = create_v021_release_fixture(tmp_path / "v026", package_version="0.26.0")
    v026_report = run_release_contract_check(v026_root, test_count_provider=lambda _: 146)
    v026_checks = {check["name"]: check for check in v026_report["checks"]}
    assert v026_report["ok"] is True
    assert "v020_proof_version_matches_pyproject" not in v026_checks
    v026_proof_check = v026_checks["v021_governed_change_proof_matches_release_gate"]
    assert v026_proof_check["ok"] is True
    assert v026_proof_check["package_version"] == "0.26.0"
    assert v026_proof_check["actual_release"] == "0.21.0"
    assert v026_proof_check["actual_target_release"] == "0.21.0"

    v022_report_path = v022_root / "benchmark" / "latest-v0.21-governed-change-report.json"
    v022_proof = json.loads(v022_report_path.read_text(encoding="utf-8"))
    v022_proof["summary"]["governed_failures"] = 1
    v022_report_path.write_text(json.dumps(v022_proof, indent=2) + "\n", encoding="utf-8")

    failed_v022_report = run_release_contract_check(v022_root, test_count_provider=lambda _: 146)
    failed_v022_checks = {check["name"]: check for check in failed_v022_report["checks"]}
    failed_v022_proof_check = failed_v022_checks["v021_governed_change_proof_matches_release_gate"]
    assert failed_v022_report["ok"] is False
    assert failed_v022_proof_check["ok"] is False
    assert failed_v022_proof_check["mismatches"] == [{"field": "summary.governed_failures", "expected": 0, "actual": 1}]


def test_release_version_check_allows_historical_semver_references_but_requires_current_marker(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)
    for name in ("README.md", "README.zh-CN.md"):
        path = root / name
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nValidated with `mcp==1.28.1`, `mcp==2.0.0`, and "
            + "`@modelcontextprotocol/client@2.0.0`.\n",
            encoding="utf-8",
        )

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    assert report["ok"] is True

    readme = root / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nHistorical AMB version: 0.8.0.\n", encoding="utf-8")
    report = run_release_contract_check(root, test_count_provider=lambda _: 146)
    assert report["ok"] is True

    readme.write_text(
        readme.read_text(encoding="utf-8") + "\nCurrent source release: `0.8.0`\n",
        encoding="utf-8",
    )
    report = run_release_contract_check(root, test_count_provider=lambda _: 146)
    check = {check["name"]: check for check in report["checks"]}["pyproject_version_matches_readmes"]

    assert check["ok"] is False
    assert check["mismatches"][0]["actual_versions"] == ["0.8.0", "0.9.0"]


def test_collect_test_count_uses_disposable_amb_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        env = kwargs["env"]
        assert isinstance(env, dict)
        runtime_dir = Path(env["AGENT_MEMORY_BRIDGE_HOME"])
        assert runtime_dir.exists()
        assert Path(env["AGENT_MEMORY_BRIDGE_DB_PATH"]) == runtime_dir / "bridge.db"
        assert Path(env["AGENT_MEMORY_BRIDGE_LOG_DIR"]) == runtime_dir / "logs"
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="639 tests collected\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert collect_test_count(tmp_path) == 639
    assert captured["cwd"] == tmp_path
    assert not Path(captured["env"]["AGENT_MEMORY_BRIDGE_HOME"]).exists()  # type: ignore[index]


def test_v027_episode_release_contract_checks_exact_surface_and_evidence(tmp_path: Path) -> None:
    root = create_v027_episode_contract_fixture(tmp_path)

    report = build_v027_episode_release_check(root, "0.27.1")

    assert report["ok"] is True
    assert report["schema_version"] == 9
    assert tuple(report["public_tool_order"]) == V027_PUBLIC_TOOL_ORDER


@pytest.mark.parametrize(
    ("relative_path", "needle", "replacement", "expected_field"),
    [
        (
            "src/agent_mem_bridge/schema.py",
            "CURRENT_SCHEMA_VERSION = 9",
            "CURRENT_SCHEMA_VERSION = 10",
            "schema.CURRENT_SCHEMA_VERSION",
        ),
        ("src/agent_mem_bridge/mcp_boundary.py", "'ack_signal')", "'wrong_tool')", "mcp_boundary.PUBLIC_TOOL_ORDER"),
        (
            "src/agent_mem_bridge/mcp_boundary.py",
            "a2e3dbbb48c87a7ce23bc4be1c8ea37c8cd176ff8f7fd2318d1374bc9e089e4a",
            "0" * 64,
            "mcp_boundary.PUBLIC_TOOL_SCHEMA_SHA256",
        ),
        ("src/agent_mem_bridge/server.py", "_PUBLIC_TOOL_HANDLERS =", "_REMOVED_HANDLERS =", "server.public_tool_set"),
        (
            "docs/CLOSED-LOOP-EPISODE.md",
            "receipt-bound memory attribution",
            "missing attribution",
            "docs/CLOSED-LOOP-EPISODE.md",
        ),
        (
            "docs/TRUST-BOUNDARY.md",
            "durable episode authority",
            "missing authority boundary",
            "docs/TRUST-BOUNDARY.md",
        ),
        ("docs/RUN-CONSOLIDATION.md", "consolidate-runs --shadow", "consolidate-runs", "docs/RUN-CONSOLIDATION.md"),
        (
            "tests/test_run_memory_attribution.py",
            "test_invalid_receipt_attribution_is_atomic_and_memory_events_require_it",
            "missing_attribution_regression",
            "tests/test_run_memory_attribution.py",
        ),
        (
            "tests/test_run_consolidation.py",
            "test_shadow_is_zero_write_and_legacy_declared_supports_stay_ineligible",
            "missing_shadow_regression",
            "tests/test_run_consolidation.py",
        ),
        (
            "src/agent_mem_bridge/run_outcome_authority.py",
            "is_strong_verified_outcome",
            "missing_strong_outcome_classifier",
            "src/agent_mem_bridge/run_outcome_authority.py",
        ),
        (
            "tests/test_v027_episode_schema.py",
            "test_v8_to_v9_backfills_terminal_times_without_mutating_episode_authority",
            "missing_v9_migration_regression",
            "tests/test_v027_episode_schema.py",
        ),
        (
            "tests/test_mcp_raw_wire.py",
            "test_raw_wire_modern_and_legacy_memory_attribution_contracts",
            "missing_raw_wire_regression",
            "tests/test_mcp_raw_wire.py",
        ),
        (
            "src/agent_mem_bridge/durable_data_policy.py",
            "_RECEIPT_SHAPED_VALUE_RE",
            "missing_receipt_policy",
            "src/agent_mem_bridge/durable_data_policy.py",
        ),
        (
            "tests/test_run_ledger.py",
            "test_receipt_shaped_values_are_rejected_before_durable_run_writes",
            "missing_receipt_regression",
            "tests/test_run_ledger.py",
        ),
        (
            "src/agent_mem_bridge/onboarding_contract.py",
            "versioned_install_tool_surface_is_explicit",
            "missing_install_regression",
            "src/agent_mem_bridge/onboarding_contract.py",
        ),
        (
            "README.md",
            "current v0.27 overview intentionally omitted",
            "examples/diagrams/amb-overview.svg",
            "README.md.current_diagram_applicability",
        ),
    ],
)
def test_v027_episode_release_contract_fails_closed_when_required_evidence_drifts(
    tmp_path: Path,
    relative_path: str,
    needle: str,
    replacement: str,
    expected_field: str,
) -> None:
    root = create_v027_episode_contract_fixture(tmp_path)
    path = root / relative_path
    path.write_text(path.read_text(encoding="utf-8").replace(needle, replacement, 1), encoding="utf-8")

    report = build_v027_episode_release_check(root, "0.27.1")

    assert report["ok"] is False
    assert any(mismatch["field"] == expected_field for mismatch in report["mismatches"])


def test_run_release_contract_check_reports_specific_mismatches(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)

    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace('version = "0.9.0"', 'version = "0.9.1"'), encoding="utf-8"
    )

    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8")
        .replace("Current source test collection: `146 tests`", "Current source test collection: `140 tests`")
        .replace("`classifier_exact_match_rate = 0.875`", "`classifier_exact_match_rate = 0.9`")
        .replace("`13` public MCP tools", "`11` public MCP tools"),
        encoding="utf-8",
    )
    production_status = root / "docs" / "PRODUCTION-STATUS.md"
    production_status.write_text(
        production_status.read_text(encoding="utf-8").replace("`146 passed`", "`140 passed`"),
        encoding="utf-8",
    )

    svg_path = root / "examples" / "diagrams" / "amb-overview.svg"
    svg_path.unlink()

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    assert report["ok"] is False
    check_names = {check["name"]: check for check in report["checks"]}
    assert check_names["pyproject_version_matches_readmes"]["ok"] is False
    assert check_names["v020_proof_version_matches_pyproject"]["ok"] is False
    assert check_names["readme_facts_match_snapshot_reports"]["ok"] is False
    assert check_names["readme_test_count_matches_collected_suite"]["ok"] is False
    assert check_names["public_mcp_tool_count_matches_server_surface"]["ok"] is False
    assert check_names["current_demo_assets_exist"]["ok"] is False
    assert check_names["current_demo_assets_exist"]["missing_assets"] == [str(svg_path)]
    assert check_names["visual_claim_inventory_is_release_hygienic"]["ok"] is False
    visual_inventory_mismatches = check_names["visual_claim_inventory_is_release_hygienic"]["mismatches"]
    assert {
        "field": "assets[examples/diagrams/amb-overview.svg].exists",
        "expected": True,
        "actual": False,
    } in visual_inventory_mismatches
    assert {mismatch["field"] for mismatch in visual_inventory_mismatches} >= {
        "claims[amb-overview-runtime-surface].release_applicability.release",
        "claims[amb-overview-proof-boundary].release_applicability.release",
    }

    gate_mismatches = [
        (None, "release", "0.20.0", "release"),
        (None, "target_release", "0.22.0", "target_release"),
        ("summary", "gate_passed", False, "summary.gate_passed"),
        ("summary", "gate_passed", 1, "summary.gate_passed"),
        ("summary", "case_count", 19, "summary.case_count"),
        (
            "summary",
            "governed_checkpoint_result_count",
            39,
            "summary.governed_checkpoint_result_count",
        ),
        ("summary", "governed_checkpoint_passes", 39, "summary.governed_checkpoint_passes"),
        (
            "summary",
            "governed_checkpoint_passes_target",
            "39/40",
            "summary.governed_checkpoint_passes_target",
        ),
        ("summary", "governed_failures", 1, "summary.governed_failures"),
        ("summary", "flat_baseline_hazards", 16, "summary.flat_baseline_hazards"),
        (
            "summary",
            "flat_baseline_hazards_expected",
            "16/20",
            "summary.flat_baseline_hazards_expected",
        ),
        (
            "summary",
            "useful_current_retention_pass",
            False,
            "summary.useful_current_retention_pass",
        ),
        (
            "summary",
            "suppress_all_can_pass",
            True,
            "summary.suppress_all_can_pass",
        ),
        ("boundaries", "public_mcp_tool_count", 11, "boundaries.public_mcp_tool_count"),
        (
            "boundaries",
            "public_mcp_surface_unchanged",
            False,
            "boundaries.public_mcp_surface_unchanged",
        ),
        ("boundaries", "config_write_count", 1, "boundaries.config_write_count"),
        (
            "boundaries",
            "durable_live_writeback_count",
            1,
            "boundaries.durable_live_writeback_count",
        ),
        ("boundaries", "auto_writeback_count", 1, "boundaries.auto_writeback_count"),
    ]
    for index, (section, field, invalid_value, mismatch_field) in enumerate(gate_mismatches):
        v021_root = create_v021_release_fixture(tmp_path / f"v021-mismatch-{index}")
        report_path = v021_root / "benchmark" / "latest-v0.21-governed-change-report.json"
        proof = json.loads(report_path.read_text(encoding="utf-8"))
        target = proof if section is None else proof[section]
        target[field] = invalid_value
        report_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

        mismatch_report = run_release_contract_check(
            v021_root,
            test_count_provider=lambda _: 146,
        )
        proof_check = next(
            item
            for item in mismatch_report["checks"]
            if item["name"] == "v021_governed_change_proof_matches_release_gate"
        )
        assert proof_check["ok"] is False
        assert {mismatch["field"] for mismatch in proof_check["mismatches"]} == {mismatch_field}

    assert_visual_claim_inventory_gaps_are_reported(tmp_path / "visual-inventory-gaps")
    assert_malformed_and_unlabeled_visual_svgs_are_reported(tmp_path / "visual-svg")
    assert_png_visual_asset_gaps_are_reported(tmp_path / "visual-png")


def test_release_contract_rejects_stale_v020_proof_version(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)
    report_path = root / "benchmark" / "latest-v0.20-clean-room-proof-report.json"
    proof = json.loads(report_path.read_text(encoding="utf-8"))
    proof["release"] = "0.8.0"
    proof["environment"]["package_version"] = "0.8.0"
    report_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    check = next(item for item in report["checks"] if item["name"] == "v020_proof_version_matches_pyproject")
    assert check["ok"] is False
    assert {item["field"] for item in check["mismatches"]} == {
        "release",
        "environment.package_version",
    }


def test_release_contract_rejects_stale_v020_cli_version_evidence(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)
    report_path = root / "benchmark" / "latest-v0.20-clean-room-proof-report.json"
    proof = json.loads(report_path.read_text(encoding="utf-8"))
    proof["cases"][0]["evidence"]["version"] = "0.8.0"
    report_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    check = next(item for item in report["checks"] if item["name"] == "v020_proof_version_matches_pyproject")
    assert check["ok"] is False
    assert check["mismatches"] == [
        {
            "field": "cases[v020-local-entrypoint-import].evidence.version",
            "expected": "0.9.0",
            "actual": "0.8.0",
        }
    ]


def test_release_contract_rejects_v021_readme_fact_drift(tmp_path: Path) -> None:
    root = create_v021_release_fixture(tmp_path)
    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(
            "v021_governed_failures = 0",
            "v021_governed_failures = 1",
        ),
        encoding="utf-8",
    )

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    checks = {check["name"]: check for check in report["checks"]}
    assert checks["v021_governed_change_proof_matches_release_gate"]["ok"] is True
    fact_check = checks["readme_facts_match_snapshot_reports"]
    assert fact_check["ok"] is False
    assert any(
        mismatch["key"] == "v021_governed_failures" and mismatch["expected"] == 0 and mismatch["actual"] == [1]
        for mismatch in fact_check["mismatches"]
    )


def test_visual_claim_current_release_must_match_pyproject(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)
    inventory_path = root / "examples" / "diagrams" / "visual-claims.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["claims"][0]["release_applicability"]["release"] = "0.8.0"
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)
    check = release_inventory_check(report)

    assert report["ok"] is False
    assert {
        "field": "claims[amb-overview-runtime-surface].release_applicability.release",
        "expected": "0.9.0",
        "actual": "0.8.0",
    } in check["mismatches"]


def test_visual_claim_historical_and_planned_releases_are_explicit(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)
    inventory_path = root / "examples" / "diagrams" / "visual-claims.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["claims"][0]["release_applicability"] = {
        "status": "historical",
        "release": "0.8.0",
    }
    inventory["claims"][1]["release_applicability"] = {
        "status": "planned",
        "release": "0.10.0",
    }
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)
    check = release_inventory_check(report)

    assert report["ok"] is True
    assert check["ok"] is True
    assert [claim["release_applicability_status"] for claim in check["claims"][:2]] == ["historical", "planned"]


def test_visual_claim_rejects_absolute_and_parent_traversal_paths(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)
    inventory_path = root / "examples" / "diagrams" / "visual-claims.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["claims"][0]["asset_path"] = "../outside.svg"
    inventory["claims"][1]["asset_path"] = "C:/outside.svg"
    inventory["claims"][2]["evidence_paths"][0] = "../README.md"
    inventory["claims"][3]["evidence_paths"][0] = "C:/outside.md"
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)
    check = release_inventory_check(report)

    assert report["ok"] is False
    assert {mismatch["field"] for mismatch in check["mismatches"]} >= {
        "claims[amb-overview-runtime-surface].asset_path",
        "claims[amb-overview-proof-boundary].asset_path",
        "claims[v022-cross-client-activation-flow].evidence_paths[0]",
        "claims[v022-receipt-anatomy].evidence_paths[0]",
    }


def test_visual_claim_rejects_symlink_paths_resolving_outside_project(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path / "project")
    outside_svg = tmp_path / "outside.svg"
    outside_svg.write_text(
        """
        <svg xmlns="http://www.w3.org/2000/svg">
          <title>Outside</title>
          <desc>Outside target.</desc>
        </svg>
        """,
        encoding="utf-8",
    )
    outside_evidence = tmp_path / "outside.md"
    outside_evidence.write_text("outside\n", encoding="utf-8")
    asset_link = root / "examples" / "diagrams" / "outside.svg"
    evidence_link = root / "docs" / "outside.md"
    make_symlink_or_skip(asset_link, outside_svg)
    make_symlink_or_skip(evidence_link, outside_evidence)

    inventory_path = root / "examples" / "diagrams" / "visual-claims.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["claims"][0]["asset_path"] = "examples/diagrams/outside.svg"
    inventory["claims"][0]["evidence_paths"][0] = "docs/outside.md"
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)
    check = release_inventory_check(report)

    assert report["ok"] is False
    assert {
        mismatch["field"]: mismatch["expected"]
        for mismatch in check["mismatches"]
        if mismatch["field"]
        in {
            "claims[amb-overview-runtime-surface].asset_path",
            "claims[amb-overview-runtime-surface].evidence_paths[0]",
        }
    } == {
        "claims[amb-overview-runtime-surface].asset_path": "path resolving inside project",
        "claims[amb-overview-runtime-surface].evidence_paths[0]": "path resolving inside project",
    }


def test_visual_claim_scans_decoded_inventory_string_values_for_private_paths(
    tmp_path: Path,
) -> None:
    root = create_release_fixture(tmp_path)
    inventory_path = root / "examples" / "diagrams" / "visual-claims.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["claims"][0]["notes"] = [
        "escaped Windows home C:\\Users\\example-user\\secret.txt",
        "escaped WSL UNC \\\\wsl.localhost\\Linux-Distro\\home\\example-user\\secret.txt",
        "plain WSL home /home/example-user/secret.txt",
    ]
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)
    check = release_inventory_check(report)

    assert report["ok"] is False
    assert "inventory.private_paths" in {mismatch["field"] for mismatch in check["mismatches"]}
    assert {match["pattern"] for match in check["inventory_private_path_matches"]} == {
        "windows_home",
        "wsl_unc_home",
        "wsl_home",
    }
    assert {match["path"] for match in check["inventory_private_path_matches"]} == {
        "$.claims[0].notes[0]",
        "$.claims[0].notes[1]",
        "$.claims[0].notes[2]",
    }


def assert_visual_claim_inventory_gaps_are_reported(root: Path) -> None:
    root = create_release_fixture(root)
    inventory_path = root / "examples" / "diagrams" / "visual-claims.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["claims"][0].pop("release_applicability")
    inventory["claims"][1]["evidence_paths"].append("docs/missing-visual-evidence.md")
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

    svg_path = root / "examples" / "diagrams" / "amb-overview.svg"
    svg_path.write_text(
        """
        <svg xmlns="http://www.w3.org/2000/svg">
          <title>Agent Memory Bridge architecture</title>
          <desc>Release-facing overview.</desc>
          <text>C:\\Users\\example-user\\private-note.txt</text>
        </svg>
        """,
        encoding="utf-8",
    )

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)
    check = next(item for item in report["checks"] if item["name"] == "visual_claim_inventory_is_release_hygienic")

    assert report["ok"] is False
    assert check["ok"] is False
    assert check["semantic_validation"] == "not_performed"
    assert {mismatch["field"] for mismatch in check["mismatches"]} == {
        "claims[amb-overview-runtime-surface].release_applicability.status",
        "claims[amb-overview-proof-boundary].evidence_paths",
        "assets[examples/diagrams/amb-overview.svg].private_paths",
    }


def assert_malformed_and_unlabeled_visual_svgs_are_reported(root: Path) -> None:
    malformed_root = create_release_fixture(root / "malformed")
    malformed_svg = malformed_root / "examples" / "diagrams" / "amb-overview.svg"
    malformed_svg.write_text("<svg><title>Agent Memory Bridge</title>", encoding="utf-8")

    malformed_report = run_release_contract_check(malformed_root, test_count_provider=lambda _: 146)
    malformed_check = next(
        item for item in malformed_report["checks"] if item["name"] == "visual_claim_inventory_is_release_hygienic"
    )

    assert malformed_report["ok"] is False
    assert {mismatch["field"] for mismatch in malformed_check["mismatches"]} == {
        "assets[examples/diagrams/amb-overview.svg].xml"
    }

    unlabeled_root = create_release_fixture(root / "unlabeled")
    unlabeled_svg = unlabeled_root / "examples" / "diagrams" / "amb-overview.svg"
    unlabeled_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><title> </title></svg>\n',
        encoding="utf-8",
    )

    unlabeled_report = run_release_contract_check(unlabeled_root, test_count_provider=lambda _: 146)
    unlabeled_check = next(
        item for item in unlabeled_report["checks"] if item["name"] == "visual_claim_inventory_is_release_hygienic"
    )

    assert unlabeled_report["ok"] is False
    assert {mismatch["field"] for mismatch in unlabeled_check["mismatches"]} == {
        "assets[examples/diagrams/amb-overview.svg].title",
        "assets[examples/diagrams/amb-overview.svg].desc",
    }


def assert_png_visual_asset_gaps_are_reported(root: Path) -> None:
    bad_label_root = create_release_fixture(root / "bad-label")
    inventory_path = bad_label_root / "examples" / "diagrams" / "visual-claims.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    png_claim = inventory["claims"][4]
    png_claim["classification"] = "evidence"
    png_claim["semantic_validation"] = "performed"
    png_claim["authenticated_claim"] = True
    png_claim["product_evidence"] = True
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

    bad_label_report = run_release_contract_check(
        bad_label_root,
        test_count_provider=lambda _: 146,
    )
    bad_label_check = next(
        item for item in bad_label_report["checks"] if item["name"] == "visual_claim_inventory_is_release_hygienic"
    )

    assert bad_label_report["ok"] is False
    assert {mismatch["field"] for mismatch in bad_label_check["mismatches"]} == {
        "claims[v022-shared-memory-hero-conceptual].classification",
        "claims[v022-shared-memory-hero-conceptual].semantic_validation",
        "claims[v022-shared-memory-hero-conceptual].authenticated_claim",
        "claims[v022-shared-memory-hero-conceptual].product_evidence",
    }

    bad_png_root = create_release_fixture(root / "bad-png")
    bad_png = bad_png_root / "examples" / "diagrams" / "v0.22-shared-memory-hero.png"
    bad_png.write_bytes(b"not-a-png C:\\Users\\example-user\\secret")

    bad_png_report = run_release_contract_check(bad_png_root, test_count_provider=lambda _: 146)
    bad_png_check = next(
        item for item in bad_png_report["checks"] if item["name"] == "visual_claim_inventory_is_release_hygienic"
    )

    assert bad_png_report["ok"] is False
    assert {mismatch["field"] for mismatch in bad_png_check["mismatches"]} == {
        "assets[examples/diagrams/v0.22-shared-memory-hero.png].private_paths",
        "assets[examples/diagrams/v0.22-shared-memory-hero.png].png_signature",
    }

    header_shaped_root = create_release_fixture(root / "header-shaped")
    header_shaped_png = header_shaped_root / "examples" / "diagrams" / "v0.22-shared-memory-hero.png"
    header_shaped_png.write_bytes(
        PNG_SIGNATURE
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (16).to_bytes(4, "big")
        + (9).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )

    header_shaped_report = run_release_contract_check(
        header_shaped_root,
        test_count_provider=lambda _: 146,
    )
    header_shaped_check = release_inventory_check(header_shaped_report)

    assert header_shaped_report["ok"] is False
    assert {mismatch["field"] for mismatch in header_shaped_check["mismatches"]} >= {
        "assets[examples/diagrams/v0.22-shared-memory-hero.png].chunks[0].crc",
        "assets[examples/diagrams/v0.22-shared-memory-hero.png].idat",
        "assets[examples/diagrams/v0.22-shared-memory-hero.png].iend",
    }

    bad_idat_root = create_release_fixture(root / "bad-idat")
    bad_idat_png = bad_idat_root / "examples" / "diagrams" / "v0.22-shared-memory-hero.png"
    bad_idat_png.write_bytes(
        PNG_SIGNATURE
        + png_chunk(
            b"IHDR",
            (16).to_bytes(4, "big") + (9).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00",
        )
        + png_chunk(b"IDAT", b"not zlib data")
        + png_chunk(b"IEND", b"")
    )

    bad_idat_report = run_release_contract_check(
        bad_idat_root,
        test_count_provider=lambda _: 146,
    )
    bad_idat_check = release_inventory_check(bad_idat_report)

    assert bad_idat_report["ok"] is False
    assert {mismatch["field"] for mismatch in bad_idat_check["mismatches"]} == {
        "assets[examples/diagrams/v0.22-shared-memory-hero.png].idat_zlib"
    }


def test_check_release_contract_script_exits_zero_for_aligned_fixture(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_release_contract.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--root", str(root)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True


# The fixture creates a fully synthetic release tree with fixed counts.
# All numeric facts (test count, benchmark values, calibration values) are
# pinned to the same values in the README, report JSON, and test_count_provider
# so the contract check passes without touching the live codebase.
# This count (146) is NOT required to match the live test count.
def create_v027_episode_contract_fixture(root: Path) -> Path:
    write_file(root / "src" / "agent_mem_bridge" / "schema.py", "CURRENT_SCHEMA_VERSION = 9\n")
    write_file(
        root / "src" / "agent_mem_bridge" / "mcp_boundary.py",
        "PUBLIC_TOOL_ORDER = " + repr(V027_PUBLIC_TOOL_ORDER) + "\n"
        "PUBLIC_TOOL_SCHEMA_SHA256 = "
        '"a2e3dbbb48c87a7ce23bc4be1c8ea37c8cd176ff8f7fd2318d1374bc9e089e4a"\n',
    )
    handler_definitions = "\n".join(f"def {name}():\n    pass\n" for name in V027_PUBLIC_TOOL_ORDER)
    handler_names = ", ".join(V027_PUBLIC_TOOL_ORDER)
    write_file(
        root / "src" / "agent_mem_bridge" / "server.py",
        handler_definitions + f"\n_PUBLIC_TOOL_HANDLERS = ({handler_names},)\n",
    )
    write_file(
        root / "docs" / "CLOSED-LOOP-EPISODE.md",
        "begin_run record_run_event get_run complete_run receipt-bound memory attribution "
        "memory_utility_shadow durable episode authority downstream learning/consolidation "
        "effects are shadow-only receipt-shaped values fileBody\n",
    )
    write_file(
        root / "docs" / "TRUST-BOUNDARY.md",
        "durable episode authority downstream learning/consolidation effects are "
        "shadow-only receipt-shaped values fileBody\n",
    )
    write_file(
        root / "docs" / "PRODUCTION-STATUS.md",
        "durable episode authority downstream learning/consolidation effects are "
        "shadow-only receipt-shaped values fileBody\n",
    )
    write_file(
        root / "docs" / "v0.27.1-announcement.md",
        "durable episode authority downstream learning/consolidation effects are "
        "shadow-only receipt-shaped values fileBody legacy_declared terminal_at snapshot_epoch\n",
    )
    write_file(
        root / "docs" / "RUN-CONSOLIDATION.md",
        "consolidate-runs --shadow does not write needs_review\n",
    )
    write_file(
        root / "tests" / "test_run_memory_attribution.py",
        "def test_memory_recalled_links_a_valid_receipt_without_persisting_the_token():\n    pass\n"
        "def test_invalid_receipt_attribution_is_atomic_and_memory_events_require_it():\n    pass\n",
    )
    write_file(
        root / "tests" / "test_run_consolidation.py",
        "def test_shadow_is_zero_write_and_legacy_declared_supports_stay_ineligible():\n    pass\n"
        "def test_cli_requires_shadow_and_renders_json():\n    pass\n",
    )
    write_file(
        root / "src" / "agent_mem_bridge" / "run_outcome_authority.py",
        'GOVERNED_V2_VERIFICATION_PROFILE = "governed-v2"\n'
        "def is_strong_verified_outcome():\n    pass\n"
        'def outcome_authority_class():\n    return "legacy_declared"\n',
    )
    write_file(
        root / "tests" / "test_v027_episode_schema.py",
        "def test_v8_to_v9_backfills_terminal_times_without_mutating_episode_authority():\n    pass\n"
        "def test_injected_v9_failure_rolls_back_ddl_data_ledger_and_user_version():\n    pass\n",
    )
    write_file(
        root / "tests" / "test_mcp_raw_wire.py",
        "def test_raw_wire_modern_and_legacy_contracts():\n    pass\n"
        "def test_raw_wire_modern_and_legacy_memory_attribution_contracts():\n    pass\n"
        "server/discover legacy_initialize\n",
    )
    write_file(
        root / "src" / "agent_mem_bridge" / "durable_data_policy.py",
        "_RECEIPT_SHAPED_VALUE_RE = None\ndef require_durable_text():\n    pass\n"
        "def normalize_durable_key():\n    pass\n",
    )
    write_file(
        root / "src" / "agent_mem_bridge" / "run_ledger.py",
        'FORBIDDEN_ARTIFACT_CONTENT_FIELDS = {"file_body", "filebody"}\n'
        "def normalize_durable_key():\n    pass\n"
        "def _is_data_uri():\n    pass\n",
    )
    write_file(
        root / "tests" / "test_run_ledger.py",
        "def test_receipt_shaped_values_are_rejected_before_durable_run_writes():\n"
        '    assert "fileBody"\n    assert "file/body"\n    assert "data:text/plain;base64"\n'
        "def test_work_item_fsm_rejections_are_atomic_and_terminal_items_cannot_reopen():\n    pass\n"
        "def test_two_processes_competing_on_the_same_terminal_cas_allow_exactly_one_writer():\n    pass\n"
        "def test_projection_drift_blocks_writes_but_get_run_returns_authority_snapshot():\n    pass\n"
        "def test_get_run_uses_one_snapshot_during_a_concurrent_append():\n    pass\n"
        "def test_legacy_declared_verified_success_is_readable_but_not_regression_authority():\n    pass\n",
    )
    write_file(
        root / "src" / "agent_mem_bridge" / "onboarding_contract.py",
        'PINNED_INSTALL_VERSION = "0.27.1"\nRELEASE_INSTALL_GATE_NOTE = "gate"\n'
        "def release_install_tool_count():\n    pass\nversioned_install_tool_surface_is_explicit = True\n",
    )
    write_file(
        root / "tests" / "test_onboarding_contract.py",
        'RELEASE_INSTALL_GATE_NOTE = "gate"\n'
        "def test_release_install_tool_count_tracks_the_release_cut():\n    pass\n"
        "def test_onboarding_contract_requires_source_checkout_wording_for_version_mismatch():\n    pass\n",
    )
    write_file(root / "README.md", "current v0.27 overview intentionally omitted\n")
    write_file(root / "README.zh-CN.md", "current v0.27 overview intentionally omitted\n")
    return root


def create_release_fixture(root: Path) -> Path:
    write_file(
        root / "pyproject.toml",
        """
        [project]
        name = "agent-memory-bridge"
        version = "0.9.0"
        """,
    )
    readme_text = """
        # Agent Memory Bridge

        Current source release: `0.9.0`

        `0.9.0` makes memory more structured and more applicable.

        - `13` public MCP tools, with most sophistication staying behind the bridge

        ## Evidence

        Current source test collection: `146 tests`
        - `question_count = 11`
        - `memory_expected_top1_accuracy = 1.0`
        - `memory_mrr = 1.0`
        - `file_scan_expected_top1_accuracy = 0.636`
        - `file_scan_mrr = 0.909`
        - `sample_count = 16`
        - `classifier_exact_match_rate = 0.875`
        - `fallback_exact_match_rate = 0.062`
        - `classifier_better_count = 13`
        - `fallback_better_count = 2`
        - `classifier_filtered_low_confidence_count = 2`
        - `case_count = 7`
        - `flat_case_pass_rate = 0.429`
        - `governed_case_pass_rate = 1.0`
        - `flat_blocked_procedure_leak_rate = 1.0`
        - `governed_blocked_procedure_leak_rate = 0.0`
        - `governed_governance_field_completeness = 1.0`
        - `signal_contention_case_count = 5`
        - `signal_contention_case_pass_rate = 1.0`
        - `unique_active_claim_rate = 1.0`
        - `duplicate_active_claim_count = 0`
        - `active_reclaim_block_rate = 1.0`
        - `stale_ack_blocked_rate = 1.0`
        - `stale_reclaim_success_rate = 1.0`
        - `pending_under_pressure_claim_rate = 1.0`
        - `initial_hard_expiry_cap_rate = 1.0`
        - `adversarial_case_count = 6`
        - `adversarial_task_count = 7`
        - `adversarial_governed_task_pass_rate = 1.0`
        - `adversarial_governed_blocked_record_leak_rate = 0.0`
        - `memory_evolution_case_count = 6`
        - `memory_evolution_task_count = 7`
        - `memory_evolution_governed_task_pass_rate = 1.0`
        - `memory_evolution_governed_blocked_record_leak_rate = 0.0`
        - `memory_evolution_governed_disposition_reason_hit_rate = 1.0`
        - `review_queue_item_count = 6`
        - `review_queue_actionable_count = 6`
        - `review_queue_hidden_lane_count = 2`
        - `review_queue_writeback_plan_count = 6`
        - `review_queue_no_auto_mutation = true`
        - `review_queue_public_mcp_surface_change = false`
        - `review_queue_item_type_count = 6`
        - `review_workflow_source_queue_item_count = 6`
        - `review_workflow_item_count = 6`
        - `review_workflow_manual_step_count = 27`
        - `review_workflow_requires_human_count = 6`
        - `review_workflow_auto_write_count = 0`
        - `review_workflow_no_auto_writeback = true`
        - `review_workflow_public_mcp_surface_change = false`
        - `review_workflow_item_type_count = 6`
        - `task_brief_used_count = 2`
        - `task_brief_ignored_count = 1`
        - `task_brief_needs_review_count = 4`
        - `task_brief_review_queue_item_count = 2`
        - `task_brief_active_signal_count = 1`
        - `task_brief_no_auto_writeback = true`
        - `task_brief_public_mcp_surface_change = false`
        - `task_brief_needs_review_source_type_count = 3`
        - `v019_case_count = 12`
        - `v019_pass_count = 12`
        - `v019_pass_rate = 1.0`
        - `v019_retrieval_case_count = 4`
        - `v019_retrieval_pass_rate = 1.0`
        - `v019_task_brief_case_count = 4`
        - `v019_task_brief_pass_rate = 1.0`
        - `v019_first_run_adoption_case_count = 4`
        - `v019_first_run_adoption_pass_rate = 1.0`
        - `v019_public_mcp_tool_count = 10`
        - `v019_public_mcp_surface_change = false`
        - `v019_client_config_write_count = 0`
        - `v019_durable_writeback_count = 0`
        - `v019_amh_required = false`
        - `v019_native_memory_comparison_required = true`
        - `v020_case_count = 6`
        - `v020_pass_count = 6`
        - `v020_pass_rate = 1.0`
        - `v020_import_sanity_pass = true`
        - `v020_stdio_round_trip_pass = true`
        - `v020_first_run_pass = true`
        - `v020_task_brief_pass = true`
        - `v020_public_mcp_tool_count = 10`
        - `v020_public_mcp_surface_change = false`
        - `v020_client_config_write_count = 0`
        - `v020_explicit_demo_memory_write_count = 1`
        - `v020_explicit_demo_signal_write_count = 0`
        - `v020_non_demo_durable_writeback_count = 0`
        - `v020_amh_required = false`
        - `v020_external_vendor_adoption_claim = false`

        ## MCP Tools

        - `store` and `recall`
        - `browse` and `stats`
        - `forget`, `feedback`, `promote`, `annotate`, and `revise`
        - `claim_signal`, `extend_signal_lease`, and `ack_signal`
        - `export`

        ![Agent Memory Bridge terminal demo](examples/demo/terminal-demo.gif)
        """
    write_file(root / "README.md", readme_text)
    write_file(root / "README.zh-CN.md", readme_text)
    write_file(root / "docs" / "PRODUCTION-STATUS.md", "Current source test collection: `146 tests`\n")
    write_file(root / "docs" / "v0.9.0-announcement.md", "Current source test collection: `146 tests`\n")
    write_file(
        root / "benchmark" / "latest-report.json",
        json.dumps(
            {
                "summary": {
                    "question_count": 11,
                    "memory_expected_top1_accuracy": 1.0,
                    "memory_mrr": 1.0,
                    "file_scan_expected_top1_accuracy": 0.636,
                    "file_scan_mrr": 0.909,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-calibration-report.json",
        json.dumps(
            {
                "summary": {
                    "sample_count": 16,
                    "classifier_exact_match_rate": 0.875,
                    "fallback_exact_match_rate": 0.062,
                    "classifier_better_count": 13,
                    "fallback_better_count": 2,
                    "classifier_filtered_low_confidence_count": 2,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-procedure-governance-report.json",
        json.dumps(
            {
                "summary": {
                    "case_count": 7,
                    "flat_case_pass_rate": 0.429,
                    "governed_case_pass_rate": 1.0,
                    "flat_blocked_procedure_leak_rate": 1.0,
                    "governed_blocked_procedure_leak_rate": 0.0,
                    "governed_governance_field_completeness": 1.0,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-signal-contention-report.json",
        json.dumps(
            {
                "summary": {
                    "case_count": 5,
                    "case_pass_rate": 1.0,
                    "unique_active_claim_rate": 1.0,
                    "duplicate_active_claim_count": 0,
                    "active_reclaim_block_rate": 1.0,
                    "stale_ack_blocked_rate": 1.0,
                    "stale_reclaim_success_rate": 1.0,
                    "pending_under_pressure_claim_rate": 1.0,
                    "initial_hard_expiry_cap_rate": 1.0,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-adversarial-memory-report.json",
        json.dumps(
            {
                "summary": {
                    "case_count": 6,
                    "task_count": 7,
                    "governed_task_pass_rate": 1.0,
                    "governed_blocked_record_leak_rate": 0.0,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-memory-evolution-report.json",
        json.dumps(
            {
                "summary": {
                    "case_count": 6,
                    "task_count": 7,
                    "governed_task_pass_rate": 1.0,
                    "governed_blocked_record_leak_rate": 0.0,
                    "governed_disposition_reason_hit_rate": 1.0,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-review-queue-report.json",
        json.dumps(
            {
                "summary": {
                    "review_queue_item_count": 6,
                    "review_queue_actionable_count": 6,
                    "review_queue_hidden_lane_count": 2,
                    "review_queue_writeback_plan_count": 6,
                    "review_queue_no_auto_mutation": True,
                    "review_queue_public_mcp_surface_change": False,
                    "review_queue_item_type_count": 6,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-review-workflow-report.json",
        json.dumps(
            {
                "summary": {
                    "review_workflow_source_queue_item_count": 6,
                    "review_workflow_item_count": 6,
                    "review_workflow_manual_step_count": 27,
                    "review_workflow_requires_human_count": 6,
                    "review_workflow_auto_write_count": 0,
                    "review_workflow_no_auto_writeback": True,
                    "review_workflow_public_mcp_surface_change": False,
                    "review_workflow_item_type_count": 6,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-task-brief-report.json",
        json.dumps(
            {
                "summary": {
                    "task_brief_used_count": 2,
                    "task_brief_ignored_count": 1,
                    "task_brief_needs_review_count": 4,
                    "task_brief_review_queue_item_count": 2,
                    "task_brief_active_signal_count": 1,
                    "task_brief_no_auto_writeback": True,
                    "task_brief_public_mcp_surface_change": False,
                    "task_brief_needs_review_source_type_count": 3,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-v0.19-adoption-proof-report.json",
        json.dumps(
            {
                "summary": {
                    "v019_case_count": 12,
                    "v019_pass_count": 12,
                    "v019_pass_rate": 1.0,
                    "v019_retrieval_case_count": 4,
                    "v019_retrieval_pass_rate": 1.0,
                    "v019_task_brief_case_count": 4,
                    "v019_task_brief_pass_rate": 1.0,
                    "v019_first_run_adoption_case_count": 4,
                    "v019_first_run_adoption_pass_rate": 1.0,
                    "v019_public_mcp_tool_count": 10,
                    "v019_public_mcp_surface_change": False,
                    "v019_client_config_write_count": 0,
                    "v019_durable_writeback_count": 0,
                    "v019_amh_required": False,
                    "v019_native_memory_comparison_required": True,
                }
            },
            indent=2,
        ),
    )
    write_file(
        root / "benchmark" / "latest-v0.20-clean-room-proof-report.json",
        json.dumps(
            {
                "release": "0.9.0",
                "environment": {"package_version": "0.9.0"},
                "cases": [
                    {
                        "id": "v020-local-entrypoint-import",
                        "evidence": {"version": "0.9.0"},
                    }
                ],
                "summary": {
                    "v020_case_count": 6,
                    "v020_pass_count": 6,
                    "v020_pass_rate": 1.0,
                    "v020_import_sanity_pass": True,
                    "v020_stdio_round_trip_pass": True,
                    "v020_first_run_pass": True,
                    "v020_task_brief_pass": True,
                    "v020_public_mcp_tool_count": 10,
                    "v020_public_mcp_surface_change": False,
                    "v020_client_config_write_count": 0,
                    "v020_explicit_demo_memory_write_count": 1,
                    "v020_explicit_demo_signal_write_count": 0,
                    "v020_non_demo_durable_writeback_count": 0,
                    "v020_amh_required": False,
                    "v020_external_vendor_adoption_claim": False,
                },
            },
            indent=2,
        ),
    )
    write_file(
        root / "src" / "agent_mem_bridge" / "server.py",
        """
        from mcp.server import MCPServer

        mcp = MCPServer(
            name="agent-memory-bridge",
            title="Agent Memory Bridge",
            description="Synthetic release-contract fixture.",
            version="0.9.0",
            log_level="WARNING",
        )

        @mcp.tool(structured_output=True)
        def store() -> None:
            return None

        @mcp.tool(structured_output=True)
        def recall() -> None:
            return None

        @mcp.tool(structured_output=True)
        def browse() -> None:
            return None

        @mcp.tool(structured_output=True)
        def stats() -> None:
            return None

        @mcp.tool(structured_output=True)
        def forget() -> None:
            return None

        @mcp.tool(structured_output=True)
        def feedback() -> None:
            return None

        @mcp.tool(structured_output=True)
        def claim_signal() -> None:
            return None

        @mcp.tool(structured_output=True)
        def extend_signal_lease() -> None:
            return None

        @mcp.tool(structured_output=True)
        def ack_signal() -> None:
            return None

        @mcp.tool(structured_output=True)
        def promote() -> None:
            return None

        @mcp.tool(structured_output=True)
        def annotate() -> None:
            return None

        @mcp.tool(structured_output=True)
        def revise() -> None:
            return None

        @mcp.tool(structured_output=True)
        def export() -> None:
            return None
        """,
    )
    write_file(
        root / "examples" / "demo" / "README.md",
        """
        # Demo

        - `terminal-demo.cast`
        - `terminal-demo.gif`
        - `terminal-demo.tape`
        """,
    )
    write_file(root / "examples" / "demo" / "terminal-demo.cast", "cast\n")
    write_file(root / "examples" / "demo" / "terminal-demo.gif", "gif\n")
    write_file(root / "examples" / "demo" / "terminal-demo.tape", "tape\n")
    write_file(
        root / "examples" / "diagrams" / "amb-overview.svg",
        """
        <svg xmlns="http://www.w3.org/2000/svg" role="img">
          <title>Agent Memory Bridge architecture</title>
          <desc>Fixture overview for the release contract.</desc>
        </svg>
        """,
    )
    write_file(
        root / "examples" / "diagrams" / "v0.22-cross-client-activation.svg",
        """
        <svg xmlns="http://www.w3.org/2000/svg" role="img">
          <title>v0.22 cross-client activation flow</title>
          <desc>Fixture activation flow for the release contract.</desc>
        </svg>
        """,
    )
    write_file(
        root / "examples" / "diagrams" / "v0.22-receipt-anatomy.svg",
        """
        <svg xmlns="http://www.w3.org/2000/svg" role="img">
          <title>v0.22 activation receipt anatomy</title>
          <desc>Fixture receipt anatomy for the release contract.</desc>
        </svg>
        """,
    )
    write_png_fixture(root / "examples" / "diagrams" / "v0.22-shared-memory-hero.png")
    write_visual_claim_inventory(root)
    sample_tests = "\n".join(
        f"def test_release_contract_sample_{index:03d}() -> None:\n    assert True\n" for index in range(146)
    )
    write_file(root / "tests" / "test_sample.py", sample_tests)
    return root


def create_v021_release_fixture(root: Path, *, package_version: str = "0.21.0") -> Path:
    create_release_fixture(root)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'version = "0.9.0"',
            f'version = "{package_version}"',
        ),
        encoding="utf-8",
    )
    for readme_name in ("README.md", "README.zh-CN.md"):
        readme = root / readme_name
        v021_facts = """

v021_case_count = 20
v021_category_count = 4
v021_flat_baseline_hazards = 17
v021_governed_case_pass_count = 20
v021_governed_failures = 0
v021_governed_checkpoint_passes = 40
v021_governed_checkpoint_result_count = 40
v021_useful_current_retention_pass = true
v021_suppress_all_can_pass = false
v021_public_mcp_tool_count = 10
v021_public_mcp_surface_change = false
v021_auto_writeback_count = 0
v021_config_write_count = 0
v021_durable_live_writeback_count = 0
"""
        readme.write_text(
            readme.read_text(encoding="utf-8").replace("`0.9.0`", f"`{package_version}`") + v021_facts,
            encoding="utf-8",
        )
    write_file(
        root / "docs" / f"v{package_version}-announcement.md",
        "Current source test collection: `146 tests`\n",
    )
    write_visual_claim_inventory(root, release=package_version)
    write_file(
        root / "benchmark" / "latest-v0.21-governed-change-report.json",
        json.dumps(
            {
                "release": "0.21.0",
                "target_release": "0.21.0",
                "summary": {
                    "gate_passed": True,
                    "case_count": 20,
                    "category_count": 4,
                    "governed_case_pass_count": 20,
                    "governed_checkpoint_result_count": 40,
                    "governed_checkpoint_passes": 40,
                    "governed_checkpoint_passes_target": "40/40",
                    "governed_failures": 0,
                    "flat_baseline_hazards": 17,
                    "flat_baseline_hazards_expected": "17/20",
                    "useful_current_retention_pass": True,
                    "suppress_all_can_pass": False,
                },
                "boundaries": {
                    "public_mcp_tool_count": 10,
                    "public_mcp_surface_unchanged": True,
                    "config_write_count": 0,
                    "durable_live_writeback_count": 0,
                    "auto_writeback_count": 0,
                },
            },
            indent=2,
        ),
    )
    return root


def write_visual_claim_inventory(root: Path, *, release: str = "0.9.0") -> None:
    write_file(
        root / "examples" / "diagrams" / "visual-claims.json",
        json.dumps(
            {
                "schema_version": 2,
                "semantic_validation": "not_performed",
                "claims": [
                    {
                        "id": "amb-overview-runtime-surface",
                        "asset_path": "examples/diagrams/amb-overview.svg",
                        "asset_type": "svg",
                        "claim": "The overview diagram presents the AMB runtime path and 10-tool public MCP surface.",
                        "evidence_paths": [
                            "README.md",
                            "README.zh-CN.md",
                            "docs/PRODUCTION-STATUS.md",
                            "src/agent_mem_bridge/server.py",
                        ],
                        "release_applicability": {
                            "status": "current",
                            "release": release,
                        },
                    },
                    {
                        "id": "amb-overview-proof-boundary",
                        "asset_path": "examples/diagrams/amb-overview.svg",
                        "asset_type": "svg",
                        "claim": "The overview diagram labels release proof gates as outside the runtime path.",
                        "evidence_paths": [
                            "README.md",
                            "docs/PRODUCTION-STATUS.md",
                            "benchmark/latest-v0.20-clean-room-proof-report.json",
                        ],
                        "release_applicability": {
                            "status": "current",
                            "release": release,
                        },
                    },
                    {
                        "id": "v022-cross-client-activation-flow",
                        "asset_path": "examples/diagrams/v0.22-cross-client-activation.svg",
                        "asset_type": "svg",
                        "claim": "The v0.22 activation flow diagram is inventoried.",
                        "evidence_paths": [
                            "README.md",
                            "docs/PRODUCTION-STATUS.md",
                        ],
                        "release_applicability": {
                            "status": "current",
                            "release": release,
                        },
                    },
                    {
                        "id": "v022-receipt-anatomy",
                        "asset_path": "examples/diagrams/v0.22-receipt-anatomy.svg",
                        "asset_type": "svg",
                        "claim": "The v0.22 receipt anatomy diagram is inventoried.",
                        "evidence_paths": [
                            "README.md",
                            "docs/PRODUCTION-STATUS.md",
                        ],
                        "release_applicability": {
                            "status": "current",
                            "release": release,
                        },
                    },
                    {
                        "id": "v022-shared-memory-hero-conceptual",
                        "asset_path": "examples/diagrams/v0.22-shared-memory-hero.png",
                        "asset_type": "png",
                        "classification": "conceptual",
                        "semantic_validation": "not_performed",
                        "authenticated_claim": False,
                        "product_evidence": False,
                        "claim": "The v0.22 hero PNG is a conceptual image-model illustration only.",
                        "evidence_paths": [
                            "README.md",
                            "docs/PRODUCTION-STATUS.md",
                        ],
                        "release_applicability": {
                            "status": "current",
                            "release": release,
                        },
                    },
                ],
            },
            indent=2,
        ),
    )


def write_png_fixture(path: Path, *, width: int = 16, height: int = 9) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scanline = b"\x00" + (b"\x00\x00\x00\x00" * width)
    idat = zlib.compress(scanline * height)
    path.write_bytes(
        PNG_SIGNATURE
        + png_chunk(
            b"IHDR",
            width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00",
        )
        + png_chunk(b"IDAT", idat)
        + png_chunk(b"IEND", b"")
    )


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        len(data).to_bytes(4, "big")
        + chunk_type
        + data
        + (binascii.crc32(chunk_type + data) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def release_inventory_check(report: dict[str, object]) -> dict[str, object]:
    return next(
        item
        for item in report["checks"]  # type: ignore[index]
        if item["name"] == "visual_claim_inventory_is_release_hygienic"
    )


def make_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symlink creation is not supported here: {exc}")


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
