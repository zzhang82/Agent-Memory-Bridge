from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from agent_mem_bridge.release_contract import run_release_contract_check


def test_run_release_contract_check_passes_for_aligned_fixture(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)

    # 146 is the fixture-internal count declared in create_release_fixture below.
    # It is intentionally not the live test count (149). The fixture is a synthetic
    # tree where the README, report JSON, and test_count_provider are all set to the
    # same fixed value so the contract check passes deterministically without running
    # pytest on the real suite.
    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    assert report["ok"] is True
    assert report["pyproject_version"] == "0.9.0"
    assert report["server_tool_count"] == 10
    assert all(check["ok"] for check in report["checks"])


def test_run_release_contract_check_reports_specific_mismatches(tmp_path: Path) -> None:
    root = create_release_fixture(tmp_path)

    pyproject = root / "pyproject.toml"
    pyproject.write_text(pyproject.read_text(encoding="utf-8").replace('version = "0.9.0"', 'version = "0.9.1"'), encoding="utf-8")

    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8")
        .replace("`146 passed`", "`140 passed`")
        .replace("`classifier_exact_match_rate = 0.875`", "`classifier_exact_match_rate = 0.9`")
        .replace("`10` public MCP tools", "`9` public MCP tools"),
        encoding="utf-8",
    )

    (root / "examples" / "demo" / "terminal-demo.gif").unlink()

    report = run_release_contract_check(root, test_count_provider=lambda _: 146)

    assert report["ok"] is False
    check_names = {check["name"]: check for check in report["checks"]}
    assert check_names["pyproject_version_matches_readmes"]["ok"] is False
    assert check_names["readme_facts_match_snapshot_reports"]["ok"] is False
    assert check_names["readme_test_count_is_aligned"]["ok"] is False
    assert check_names["public_mcp_tool_count_matches_server_surface"]["ok"] is False
    assert check_names["current_demo_assets_exist"]["ok"] is False


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

        `0.9.0` makes memory more structured and more applicable.

        - `10` public MCP tools, with most sophistication staying behind the bridge

        ## Evidence

        - `pytest` currently passes with `146 passed`
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

        ## MCP Tools

        - `store` and `recall`
        - `browse` and `stats`
        - `forget` and `promote`
        - `claim_signal`, `extend_signal_lease`, and `ack_signal`
        - `export`

        ![Agent Memory Bridge terminal demo](examples/demo/terminal-demo.gif)
        """
    write_file(root / "README.md", readme_text)
    write_file(root / "README.zh-CN.md", readme_text)
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
        root / "src" / "agent_mem_bridge" / "server.py",
        """
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("agent-memory-bridge")

        @mcp.tool()
        def store():
            return None

        @mcp.tool()
        def recall():
            return None

        @mcp.tool()
        def browse():
            return None

        @mcp.tool()
        def stats():
            return None

        @mcp.tool()
        def forget():
            return None

        @mcp.tool()
        def claim_signal():
            return None

        @mcp.tool()
        def extend_signal_lease():
            return None

        @mcp.tool()
        def ack_signal():
            return None

        @mcp.tool()
        def promote():
            return None

        @mcp.tool()
        def export():
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
    write_file(root / "examples" / "diagrams" / "amb-overview.png", "png\n")
    sample_tests = "\n".join(
        f"def test_release_contract_sample_{index:03d}() -> None:\n    assert True\n"
        for index in range(146)
    )
    write_file(root / "tests" / "test_sample.py", sample_tests)
    return root


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
