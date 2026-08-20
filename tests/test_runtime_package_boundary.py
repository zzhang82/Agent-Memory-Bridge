from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from agent_mem_bridge.onboarding import TOOL_NAMES
from agent_mem_bridge.release_contract import load_server_tool_names

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_distribution_excludes_evidence_package_and_keeps_source_reproducibility(tmp_path: Path) -> None:
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
    assert not any(entry.startswith("tools/evidence/") for entry in wheel_entries)
    assert "agent_mem_bridge/v019_adoption_proof.py" not in wheel_entries
    assert "agent_mem_bridge/task_memory_benchmark.py" not in wheel_entries

    with tarfile.open(sdist_path, "r:gz") as sdist:
        sdist_entries = set(sdist.getnames())
    root = next(entry.split("/", 1)[0] for entry in sdist_entries if entry.endswith("/pyproject.toml"))
    assert f"{root}/tools/evidence/v019_adoption_proof.py" in sdist_entries
    assert f"{root}/tools/evidence/task_memory_benchmark.py" in sdist_entries
    assert f"{root}/scripts/run_v019_adoption_proof.py" in sdist_entries
    assert f"{root}/scripts/_source_imports.py" in sdist_entries
    assert f"{root}/benchmark/v0.19-fixture-manifest.json" in sdist_entries


def test_runtime_entrypoint_modules_do_not_import_source_only_evidence() -> None:
    runtime_sources = [
        ROOT / "src" / "agent_mem_bridge" / name
        for name in ("__main__.py", "cli.py", "server.py", "setup_apply.py", "first_run.py", "onboarding.py")
    ]

    assert all(
        "tools.evidence" not in path.read_text(encoding="utf-8")
        and "from ._evidence" not in path.read_text(encoding="utf-8")
        for path in runtime_sources
    )
    assert load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py") == TOOL_NAMES
    assert len(TOOL_NAMES) == 17
