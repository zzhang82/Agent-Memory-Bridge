import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_mem_bridge.activation_stress as activation_stress
from agent_mem_bridge._temporary_store import ScopedTemporaryMemoryStore
from agent_mem_bridge.activation_stress import (
    DEFAULT_ACTIVATION_STRESS_PACK_PATH,
    render_activation_stress_text,
    run_activation_stress_pack,
)


def test_run_activation_stress_pack_matches_default_manifest(tmp_path: Path, monkeypatch) -> None:
    runtime_dirs: list[Path] = []
    stores: list[ScopedTemporaryMemoryStore] = []

    def capture_store(*args: object, **kwargs: object) -> ScopedTemporaryMemoryStore:
        store = ScopedTemporaryMemoryStore(*args, **kwargs)  # type: ignore[arg-type]
        stores.append(store)
        return store

    def create_runtime_dir(*, prefix: str) -> str:
        runtime_dir = tmp_path / f"activation-stress-{len(runtime_dirs)}"
        runtime_dir.mkdir()
        runtime_dirs.append(runtime_dir)
        return str(runtime_dir)

    def checked_rmtree(path: Path) -> None:
        store = next(store for store in stores if store.db_path.parent == path)
        assert store.open_connection_count == 0
        shutil.rmtree(path)

    monkeypatch.setattr(activation_stress, "ScopedTemporaryMemoryStore", capture_store)
    monkeypatch.setattr(activation_stress, "tempfile", SimpleNamespace(mkdtemp=create_runtime_dir))
    monkeypatch.setattr(activation_stress, "shutil", SimpleNamespace(rmtree=checked_rmtree))
    report = run_activation_stress_pack(pack_path=DEFAULT_ACTIVATION_STRESS_PACK_PATH)

    assert report["summary"]["case_count"] == 16
    assert report["summary"]["pass_count"] == 16
    assert report["summary"]["pass_rate"] == 1.0
    assert report["summary"]["reviewed_case_count"] == 13
    assert report["summary"]["replay_scenario_count"] == 3
    assert report["bucket_summaries"]["promote"]["case_count"] == 4
    assert report["bucket_summaries"]["candidate"]["case_count"] == 3
    assert report["bucket_summaries"]["block"]["case_count"] == 8
    assert report["bucket_summaries"]["red-flag"]["case_count"] == 1
    assert report["bucket_summaries"]["red-flag"]["pass_count"] == 1
    assert report["cleanup_posture"]["touches_live_data"] is False
    assert report["cleanup_posture"]["runtime_cleanup"] == "automatic-temp-store-removal"
    assert len(report["cleanup_posture"]["durable_regression_ids"]) == 16
    assert all(result["match"] is True for result in report["results"])
    assert runtime_dirs
    assert all(not runtime_dir.exists() for runtime_dir in runtime_dirs)

    failed_runtime_dir = tmp_path / "activation-stress-cleanup-failure"
    failed_runtime_dir.mkdir()
    failed_scenario = json.loads(DEFAULT_ACTIVATION_STRESS_PACK_PATH.read_text(encoding="utf-8"))["replay_scenarios"][0]

    def failing_rmtree(path: Path) -> None:
        assert path == failed_runtime_dir
        assert stores[-1].open_connection_count == 0
        raise OSError("simulated activation cleanup failure")

    monkeypatch.setattr(
        activation_stress,
        "tempfile",
        SimpleNamespace(mkdtemp=lambda *, prefix: str(failed_runtime_dir)),
    )
    monkeypatch.setattr(activation_stress, "shutil", SimpleNamespace(rmtree=failing_rmtree))
    with pytest.raises(OSError, match="simulated activation cleanup failure"):
        activation_stress._run_replay_scenario(failed_scenario)
    assert failed_runtime_dir.exists()
    shutil.rmtree(failed_runtime_dir)


def test_run_activation_stress_pack_can_filter_to_one_bucket() -> None:
    report = run_activation_stress_pack(
        pack_path=DEFAULT_ACTIVATION_STRESS_PACK_PATH,
        buckets=("red-flag",),
    )

    assert report["summary"]["case_count"] == 1
    assert report["summary"]["pass_count"] == 1
    assert set(report["bucket_summaries"]) == {"red-flag"}
    assert report["results"][0]["id"] == "r1"
    assert report["results"][0]["kind"] == "replay"
    assert "belief-to-domain-note-ratio" in report["results"][0]["actual"]["final_red_flags"]


def test_render_activation_stress_text_includes_summary_and_cleanup() -> None:
    report = run_activation_stress_pack(
        pack_path=DEFAULT_ACTIVATION_STRESS_PACK_PATH,
        buckets=("candidate", "red-flag"),
    )

    rendered = render_activation_stress_text(report)

    assert "Activation Stress Pack" in rendered
    assert "Summary" in rendered
    assert "Cleanup" in rendered
    assert "touches_live_data: False" in rendered
    assert "Failures" in rendered
