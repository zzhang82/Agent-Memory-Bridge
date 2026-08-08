import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_mem_bridge.belief_review as belief_review
from agent_mem_bridge._temporary_store import ScopedTemporaryMemoryStore
from agent_mem_bridge.belief_review import (
    DEFAULT_REVIEWED_SAMPLES_PATH,
    run_belief_review,
    run_belief_review_case,
)


def test_run_belief_review_matches_default_reviewed_samples(tmp_path: Path, monkeypatch) -> None:
    runtime_dirs: list[Path] = []
    stores: list[ScopedTemporaryMemoryStore] = []

    def capture_store(*args: object, **kwargs: object) -> ScopedTemporaryMemoryStore:
        store = ScopedTemporaryMemoryStore(*args, **kwargs)  # type: ignore[arg-type]
        stores.append(store)
        return store

    def create_runtime_dir(*, prefix: str) -> str:
        runtime_dir = tmp_path / f"belief-review-{len(runtime_dirs)}"
        runtime_dir.mkdir()
        runtime_dirs.append(runtime_dir)
        return str(runtime_dir)

    def checked_rmtree(path: Path) -> None:
        store = next(store for store in stores if store.db_path.parent == path)
        assert store.open_connection_count == 0
        shutil.rmtree(path)

    monkeypatch.setattr(belief_review, "ScopedTemporaryMemoryStore", capture_store)
    monkeypatch.setattr(belief_review, "tempfile", SimpleNamespace(mkdtemp=create_runtime_dir))
    monkeypatch.setattr(belief_review, "shutil", SimpleNamespace(rmtree=checked_rmtree))
    report = run_belief_review(reviewed_samples_path=DEFAULT_REVIEWED_SAMPLES_PATH)

    assert report["filters"]["slices"] == []
    assert report["summary"]["sample_count"] == 13
    assert report["summary"]["exact_match_count"] == 13
    assert report["summary"]["exact_match_rate"] == 1.0
    assert report["summary"]["belief_count"] == 4
    assert report["summary"]["candidate_only_count"] == 9
    assert report["summary"]["blocking_reason_counts"] == {
        "blocked-contradiction": 5,
        "blocked-low-support": 2,
        "blocked-stability": 1,
        "stale": 1,
    }
    assert all(result["match"] is True for result in report["results"])
    assert runtime_dirs
    assert all(not runtime_dir.exists() for runtime_dir in runtime_dirs)

    failed_runtime_dir = tmp_path / "belief-review-cleanup-failure"
    failed_runtime_dir.mkdir()
    failed_sample = json.loads(DEFAULT_REVIEWED_SAMPLES_PATH.read_text(encoding="utf-8"))[0]

    def failing_rmtree(path: Path) -> None:
        assert path == failed_runtime_dir
        assert stores[-1].open_connection_count == 0
        raise OSError("simulated review cleanup failure")

    monkeypatch.setattr(
        belief_review,
        "tempfile",
        SimpleNamespace(mkdtemp=lambda *, prefix: str(failed_runtime_dir)),
    )
    monkeypatch.setattr(belief_review, "shutil", SimpleNamespace(rmtree=failing_rmtree))
    with pytest.raises(OSError, match="simulated review cleanup failure"):
        run_belief_review_case(failed_sample)
    assert failed_runtime_dir.exists()
    shutil.rmtree(failed_runtime_dir)


def test_run_belief_review_slice_summaries_show_expected_shape() -> None:
    report = run_belief_review(reviewed_samples_path=DEFAULT_REVIEWED_SAMPLES_PATH)

    assert report["slice_summaries"]["contradiction-quality"]["sample_count"] == 6
    assert report["slice_summaries"]["contradiction-quality"]["belief_count"] == 3
    assert report["slice_summaries"]["contradiction-quality"]["blocking_reason_counts"] == {"blocked-contradiction": 3}
    assert report["slice_summaries"]["contradiction-watchlist"]["sample_count"] == 1
    assert report["slice_summaries"]["contradiction-watchlist"]["blocking_reason_counts"] == {
        "blocked-contradiction": 1
    }
    assert report["slice_summaries"]["startup-protocol"]["belief_count"] == 1
    assert report["slice_summaries"]["startup-protocol"]["exact_match_rate"] == 1.0
    assert report["slice_summaries"]["runtime"]["blocking_reason_counts"] == {"blocked-contradiction": 1}
    assert report["slice_summaries"]["maintenance"]["blocking_reason_counts"] == {"stale": 1}
    assert report["slice_summaries"]["memory-shaping"]["blocking_reason_counts"] == {"blocked-low-support": 1}


def test_run_belief_review_can_filter_to_specific_slice() -> None:
    report = run_belief_review(
        reviewed_samples_path=DEFAULT_REVIEWED_SAMPLES_PATH,
        slices=("contradiction-quality",),
    )

    assert report["filters"]["slices"] == ["contradiction-quality"]
    assert report["summary"]["sample_count"] == 6
    assert report["summary"]["exact_match_count"] == 6
    assert report["summary"]["belief_count"] == 3
    assert report["summary"]["candidate_only_count"] == 3
    assert report["summary"]["blocking_reason_counts"] == {"blocked-contradiction": 3}
    assert set(report["slice_summaries"]) == {"contradiction-quality"}
    assert all(result["slice"] == "contradiction-quality" for result in report["results"])
    assert any(result["id"] == "b10" and result["actual"]["belief"] is True for result in report["results"])
    assert any(result["id"] == "b11" and result["actual"]["belief"] is True for result in report["results"])
    assert any(result["id"] == "b12" and result["actual"]["belief"] is True for result in report["results"])
