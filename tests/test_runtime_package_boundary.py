from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from agent_mem_bridge.onboarding import TOOL_NAMES
from agent_mem_bridge.release_contract import load_server_tool_names

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "tools" / "evidence" / "p2d_pre_move_module_inventory.json"


def test_pre_move_inventory_is_complete_mutually_exclusive_and_accounts_for_moves() -> None:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    modules = inventory["modules"]
    primary_counts = inventory["primary_class_counts"]
    runtime_classes = {"runtime_required", "cli_runtime", "compatibility_required"}

    assert inventory["baseline_sha"] == "9aeffe1c9c78201212c5060ff637a39b2838cc99"
    assert inventory["classification_kind"] == "mutually_exclusive_primary"
    assert inventory["module_count"] == 100
    assert len(modules) == inventory["module_count"]
    assert len({item["module"] for item in modules}) == inventory["module_count"]
    assert sum(primary_counts.values()) == inventory["module_count"]
    assert {item["primary_class"] for item in modules} <= set(primary_counts)
    assignments = {item["module"]: item["primary_class"] for item in modules}
    assert all(assignments[module] not in runtime_classes for module in inventory["moved_modules"])
    assert assignments["codex_rollout"] == "runtime_required"


def test_runtime_distribution_excludes_evidence_package_and_executes_extracted_sdist_runner(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    completed = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--sdist", "--outdir", str(dist_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    wheel_path = next(dist_dir.glob("*.whl"))
    sdist_path = next(dist_dir.glob("*.tar.gz"))

    with zipfile.ZipFile(wheel_path) as wheel:
        wheel_entries = set(wheel.namelist())
    assert {
        "agent_mem_bridge/cli.py",
        "agent_mem_bridge/server.py",
        "agent_mem_bridge/storage.py",
        "agent_mem_bridge/setup_apply.py",
        "agent_mem_bridge/first_run.py",
    }.issubset(wheel_entries)
    assert not any(entry.startswith("tools/") for entry in wheel_entries)
    assert "agent_mem_bridge/v019_adoption_proof.py" not in wheel_entries
    assert "agent_mem_bridge/task_memory_benchmark.py" not in wheel_entries

    with tarfile.open(sdist_path, "r:gz") as sdist:
        sdist_entries = set(sdist.getnames())
        root = next(entry.split("/", 1)[0] for entry in sdist_entries if entry.endswith("/pyproject.toml"))
        extracted_parent = tmp_path / "extracted"
        sdist.extractall(extracted_parent)
    assert f"{root}/tools/__init__.py" in sdist_entries
    assert f"{root}/tools/evidence/__init__.py" in sdist_entries
    assert f"{root}/tools/evidence/v019_adoption_proof.py" in sdist_entries
    assert f"{root}/tools/evidence/p2d_pre_move_module_inventory.json" in sdist_entries
    assert f"{root}/scripts/run_v019_adoption_proof.py" in sdist_entries
    assert f"{root}/scripts/_source_imports.py" in sdist_entries
    assert f"{root}/benchmark/v0.19-fixture-manifest.json" in sdist_entries

    extracted_root = extracted_parent / root
    report_path = tmp_path / "v019-extracted-sdist-report.json"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["HOME"] = str(tmp_path / "home")
    runner = subprocess.run(
        [
            sys.executable,
            "scripts/run_v019_adoption_proof.py",
            "--report-path",
            str(report_path),
        ],
        cwd=extracted_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert runner.returncode == 0, runner.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["v019_case_count"] == 12
    assert report["summary"]["v019_pass_rate"] == 1.0


def test_runtime_entrypoint_modules_do_not_import_source_only_evidence() -> None:
    runtime_sources = [
        ROOT / "src" / "agent_mem_bridge" / name
        for name in ("__main__.py", "cli.py", "server.py", "setup_apply.py", "first_run.py", "onboarding.py")
    ]

    assert all("tools.evidence" not in path.read_text(encoding="utf-8") for path in runtime_sources)
    assert load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py") == TOOL_NAMES
    assert len(TOOL_NAMES) == 17
