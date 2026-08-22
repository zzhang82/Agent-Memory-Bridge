from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from agent_mem_bridge.repository_bootstrap import compile_repository_snapshot, render_snapshot_markdown


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture repo"
    repo.mkdir()
    (repo / "README.md").write_text("Fixture project\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname="fixture"\nrequires-python=">=3.11"\n', encoding="utf-8")
    (repo / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    return repo


def facts(snapshot: dict[str, object], key: str) -> list[dict[str, object]]:
    return [item for item in snapshot["facts"] if item["key"] == key]  # type: ignore[index]


def test_deterministic_provenance_and_markdown(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    first = compile_repository_snapshot(repo)
    second = compile_repository_snapshot(repo)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["commit"] == git_sha(repo)
    assert facts(first, "python_package")[0]["source"] == "pyproject.toml"
    assert facts(first, "python_package")[0]["authority"] == "derived"
    rendered = render_snapshot_markdown(first)
    assert "not durable project memory" in rendered
    assert "source: pyproject.toml" in rendered


def git_sha(repo: Path) -> str:
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def test_new_commit_changes_snapshot_and_old_is_not_current(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    before = compile_repository_snapshot(repo)
    (repo / "README.md").write_text("Changed fixture\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "change")
    after = compile_repository_snapshot(repo)
    assert after["commit"] != before["commit"]
    assert after["commit"] == git_sha(repo)
    assert all(item["commit"] == after["commit"] for item in after["facts"])


def test_no_network_and_no_durable_memory(tmp_path: Path, monkeypatch) -> None:
    repo = fixture_repo(tmp_path)
    calls: list[list[str]] = []
    import agent_mem_bridge.repository_bootstrap as module

    original = module.subprocess.run

    def local_only(cmd, *args, **kwargs):
        calls.append(list(cmd))
        assert cmd[0] == "git"
        assert all(part not in {"fetch", "pull", "clone"} for part in cmd)
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", local_only)
    snapshot = module.compile_repository_snapshot(repo)
    assert snapshot["authority"] == "derived_repository_knowledge"
    assert not (repo / "bridge.db").exists()
    assert calls


def test_symlink_escape_binary_large_and_secret_exclusion(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    shutil.rmtree(repo / ".git")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (repo / "escaped.txt").symlink_to(outside)
    (repo / ".env").write_text("TOKEN=do-not-read", encoding="utf-8")
    (repo / "Dockerfile").write_bytes(b"\x00\x01")
    (repo / "requirements.txt").write_bytes(b"x" * 70_000)
    snapshot = compile_repository_snapshot(repo)
    reasons = " ".join(item["reason"] for item in snapshot["excluded"])
    assert "symlink escapes" in reasons
    assert "binary file" in reasons
    assert "65536" in reasons
    assert "secret-like" in reasons or "escaped" not in reasons


def test_malformed_manifest_missing_optional_and_unicode(tmp_path: Path) -> None:
    repo = tmp_path / "ユニコード"
    repo.mkdir()
    (repo / "package.json").write_text("{not-json", encoding="utf-8")
    snapshot = compile_repository_snapshot(repo)
    assert snapshot["repository"] == repo.name
    assert any("malformed JSON" in item["reason"] for item in snapshot["excluded"])
    assert snapshot["uncertain"]


def test_non_git_directory_is_explicitly_uncertain(tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "README.md").write_text("plain", encoding="utf-8")
    snapshot = compile_repository_snapshot(repo)
    assert snapshot["commit"] is None
    assert any("git unavailable" in item["reason"] for item in snapshot["uncertain"])
    assert facts(snapshot, "project_document")


def test_invalid_path_is_bounded(tmp_path: Path) -> None:
    snapshot = compile_repository_snapshot(tmp_path / "missing")
    assert snapshot["facts"] == []
    assert snapshot["excluded"][0]["reason"] == "not a directory"


def test_cli_surface_does_not_add_mcp_tools() -> None:
    from agent_mem_bridge.cli import _build_parser

    parser = _build_parser()
    assert parser.parse_args(["bootstrap-repo", "."]).command == "bootstrap-repo"


def test_dirty_git_worktree_fails_closed_without_content_facts(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    before = compile_repository_snapshot(repo)
    (repo / "README.md").write_text("changed but uncommitted\n", encoding="utf-8")
    dirty = compile_repository_snapshot(repo)
    assert before["binding"] == "git_commit"
    assert before["worktree_clean"] is True
    assert dirty["binding"] == "unavailable"
    assert dirty["worktree_clean"] is False
    assert dirty["reason"] == "dirty_worktree"
    assert dirty["facts"] == []
    assert any("withheld" in item["reason"] for item in dirty["uncertain"])
    dirty_markdown = render_snapshot_markdown(dirty).lower()
    assert "explicitly commit-bound because the worktree is clean" not in dirty_markdown
    assert "not commit-bound" in dirty_markdown


def test_directory_listing_failure_is_bounded(tmp_path: Path, monkeypatch) -> None:
    repo = fixture_repo(tmp_path)
    import agent_mem_bridge.repository_bootstrap as module

    original = Path.iterdir

    def fail_listing(self: Path):
        if self == repo:
            raise OSError("simulated listing failure")
        return original(self)

    monkeypatch.setattr(Path, "iterdir", fail_listing)
    snapshot = module.compile_repository_snapshot(repo)
    assert snapshot["facts"]
    assert any("directory listing failed" in item["reason"] for item in snapshot["uncertain"])


def test_clean_worktree_report_is_explicitly_commit_bound(tmp_path: Path) -> None:
    snapshot = compile_repository_snapshot(fixture_repo(tmp_path))
    assert snapshot["binding"] == "git_commit"
    assert snapshot["worktree_clean"] is True
    assert all(item["commit"] == snapshot["commit"] for item in snapshot["facts"])
    markdown = render_snapshot_markdown(snapshot)
    assert "Binding: git_commit" in markdown
    assert "Worktree clean: True" in markdown
