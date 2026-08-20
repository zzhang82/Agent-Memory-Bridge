from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_mem_bridge import cli as cli_module
from agent_mem_bridge import setup_apply as apply_module
from agent_mem_bridge.cli import main
from agent_mem_bridge.setup_apply import (
    apply_setup_plan,
    capture_setup_apply_snapshot,
    rollback_setup_plan,
)
from agent_mem_bridge.setup_planner import SetupPlan, build_setup_plan


def _plan(tmp_path: Path, client: str, *, platform: str = "linux") -> SetupPlan:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return build_setup_plan(
        clients=[client],
        cwd=project,
        home=tmp_path / "home",
        environ={},
        platform=platform,
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "bridge-home",
        bridge_config_path=tmp_path / "bridge-config.toml",
        which=lambda command: f"/usr/bin/{command}",
    )


def _target(plan: SetupPlan) -> Path:
    path = plan.clients[0].config_path
    assert path is not None
    return Path(path)


def _write(path: Path, payload: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, indent=2).encode("utf-8")
    path.write_bytes(raw)
    return raw


def _apply(plan: SetupPlan) -> tuple[object, SetupPlan]:
    snapshot = capture_setup_apply_snapshot(plan)
    current = _plan(Path(plan.clients[0].config_path).parents[1], plan.clients[0].client)
    return apply_setup_plan(plan, current_plan=current, snapshot=snapshot), current


def _rebuild(tmp_path: Path, client: str, *, platform: str = "linux") -> SetupPlan:
    return _plan(tmp_path, client, platform=platform)


def test_json_create_writes_only_target_receipt_and_exact_parent(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "vscode")
    target = _target(plan)
    assert not target.exists()
    snapshot = capture_setup_apply_snapshot(plan)

    result = apply_setup_plan(plan, current_plan=_rebuild(tmp_path, "vscode"), snapshot=snapshot)

    assert result.write_count == 1
    assert result.backup_count == 0
    assert result.clients[0].status == "created"
    assert result.clients[0].verification == "passed"
    assert target.is_file()
    assert (target.parent / f".{target.name}.amb-setup-receipt.json").is_file()
    assert not list(target.parent.glob(f".{target.name}.amb-*.tmp"))
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "agentMemoryBridge" in payload["servers"]
    assert not (tmp_path / "bridge-home").exists()


def test_json_merge_preserves_unrelated_structure_backup_and_privacy(tmp_path: Path) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    original = _write(
        target,
        {
            "other": {"keep": [1, 2, 3]},
            "mcpServers": {"unrelated-mcp-server": {"command": "private", "env": {"API_KEY": "super-secret"}}},
        },
    )
    plan = _rebuild(tmp_path, "claude-code")
    snapshot = capture_setup_apply_snapshot(plan)

    result = apply_setup_plan(plan, current_plan=_rebuild(tmp_path, "claude-code"), snapshot=snapshot)

    client = result.clients[0]
    assert client.status == "merged"
    assert result.write_count == 1
    assert result.backup_count == 1
    assert client.backup_path is not None
    assert Path(client.backup_path).read_bytes() == original
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["other"] == {"keep": [1, 2, 3]}
    assert written["mcpServers"]["unrelated-mcp-server"] == {
        "command": "private",
        "env": {"API_KEY": "super-secret"},
    }
    rendered = json.dumps(result.as_dict())
    assert "super-secret" not in rendered
    assert "unrelated-mcp-server" not in rendered


def test_json_update_only_replaces_amb_owned_entry(tmp_path: Path) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    desired = json.loads(initial.clients[0].proposed_fragment)
    desired["mcpServers"]["agentMemoryBridge"]["args"] = ["-m", "agent_mem_bridge", "old"]
    desired["mcpServers"]["other"] = {"command": "unchanged"}
    _write(target, desired)
    plan = _rebuild(tmp_path, "claude-code")
    assert plan.clients[0].recommended_action == "would_update"

    result = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )

    assert result.clients[0].status == "updated"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["other"] == {"command": "unchanged"}
    assert (
        payload["mcpServers"]["agentMemoryBridge"]
        == json.loads(plan.clients[0].proposed_fragment)["mcpServers"]["agentMemoryBridge"]
    )


@pytest.mark.parametrize("payload", [{"mcpServers": {"agentMemoryBridge": {"command": "foreign"}}}, "not-json"])
def test_conflict_or_unreadable_json_is_never_mutated(tmp_path: Path, payload: object) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    if isinstance(payload, str):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    else:
        _write(target, payload)
    before = target.read_bytes()
    plan = _rebuild(tmp_path, "claude-code")

    result = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )

    assert result.write_count == 0
    assert result.backup_count == 0
    assert result.clients[0].status == "skipped_manual_review"
    assert target.read_bytes() == before


def test_opencode_alternate_custom_marker_is_nonwritable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    custom = tmp_path / "custom.json"
    plan = build_setup_plan(
        clients=["opencode"],
        cwd=project,
        home=tmp_path / "home",
        environ={"OPENCODE_CONFIG": str(custom)},
        platform="linux",
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "bridge-home",
        bridge_config_path=tmp_path / "bridge-config.toml",
        which=lambda command: "/usr/bin/opencode",
    )

    result = apply_setup_plan(plan, current_plan=plan, snapshot=capture_setup_apply_snapshot(plan))

    assert plan.clients[0].recommended_action == "manual_review"
    assert result.clients[0].status == "skipped_manual_review"
    assert not custom.exists()


def test_changed_since_preview_refuses_write(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "claude-code")
    target = _target(plan)
    snapshot = capture_setup_apply_snapshot(plan)
    _write(target, {"mcpServers": {"other": {"command": "changed-after-preview"}}})

    result = apply_setup_plan(plan, current_plan=_rebuild(tmp_path, "claude-code"), snapshot=snapshot)

    assert result.clients[0].status == "changed_since_plan"
    assert result.write_count == 0
    assert "changed-after-preview" in target.read_text(encoding="utf-8")


def test_repeated_apply_is_idempotent_without_new_backup(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "claude-code")
    first = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )
    second_plan = _rebuild(tmp_path, "claude-code")

    second = apply_setup_plan(
        second_plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(second_plan),
    )

    assert first.write_count == 1
    assert second.clients[0].status == "unchanged"
    assert second.write_count == 0
    assert second.backup_count == 0


def test_rollback_restores_exact_existing_bytes_and_refuses_user_edit(tmp_path: Path) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    original = _write(target, {"mcpServers": {"other": {"env": {"TOKEN": "secret"}}}})
    plan = _rebuild(tmp_path, "claude-code")
    applied = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )
    assert applied.clients[0].backup_path is not None

    rolled_back = rollback_setup_plan(_rebuild(tmp_path, "claude-code"))
    assert rolled_back.clients[0].status == "restored"
    assert target.read_bytes() == original

    reapply_plan = _rebuild(tmp_path, "claude-code")
    apply_setup_plan(
        reapply_plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(reapply_plan),
    )
    target.write_text('{"mcpServers": {"user": {}}}', encoding="utf-8")
    refused = rollback_setup_plan(_rebuild(tmp_path, "claude-code"))
    assert refused.clients[0].status == "skipped_manual_review"
    assert "user" in target.read_text(encoding="utf-8")


def test_rollback_removes_unchanged_created_target_but_not_nonempty_parent(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "vscode")
    target = _target(plan)
    applied = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "vscode"),
        snapshot=capture_setup_apply_snapshot(plan),
    )
    assert applied.clients[0].status == "created"
    target.parent.joinpath("user-file.txt").write_text("keep", encoding="utf-8")

    rolled_back = rollback_setup_plan(_rebuild(tmp_path, "vscode"))

    assert rolled_back.clients[0].status == "removed_created"
    assert not target.exists()
    assert target.parent.is_dir()
    assert target.parent.joinpath("user-file.txt").read_text(encoding="utf-8") == "keep"


def test_backup_or_serialization_failure_preserves_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    original = _write(target, {"mcpServers": {"other": {"command": "keep"}}})
    plan = _rebuild(tmp_path, "claude-code")

    real_create_backup = apply_module._create_backup
    monkeypatch.setattr(apply_module, "_create_backup", lambda *_args: (_ for _ in ()).throw(OSError("backup")))
    backup_failure = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )
    assert backup_failure.clients[0].status == "failed"
    assert target.read_bytes() == original

    monkeypatch.setattr(apply_module, "_create_backup", real_create_backup)
    monkeypatch.setattr(
        apply_module,
        "_serialize_json",
        lambda _payload: (_ for _ in ()).throw(ValueError("serialize")),
    )
    serialization_failure = apply_setup_plan(
        _rebuild(tmp_path, "claude-code"),
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(_rebuild(tmp_path, "claude-code")),
    )
    assert serialization_failure.clients[0].status == "failed"
    assert target.read_bytes() == original


def test_post_write_verification_failure_reports_actual_write_and_keeps_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path, "claude-code")
    target = _target(plan)
    monkeypatch.setattr(apply_module, "_verify_json_target", lambda *_args: False)

    result = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )

    assert result.clients[0].status == "failed"
    assert result.clients[0].changed is True
    assert result.write_count == 1
    assert target.is_file()
    assert (target.parent / f".{target.name}.amb-setup-receipt.json").is_file()


def test_cli_apply_confirmation_decline_eof_and_yes_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    plan = _plan(tmp_path, "claude-code")
    namespace = SimpleNamespace(
        client=["claude-code"],
        cwd=tmp_path / "project",
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "bridge-home",
        config_path=tmp_path / "bridge-config.toml",
        json=False,
        apply=True,
        yes=False,
        rollback=False,
    )
    monkeypatch.setattr(cli_module, "build_setup_plan", lambda **_kwargs: plan)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert cli_module._run_setup(namespace) == 0
    assert not _target(plan).exists()
    assert "No changes were made." in capsys.readouterr().out

    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))
    assert cli_module._run_setup(namespace) == 0
    assert not _target(plan).exists()

    assert main(["setup", "--yes"]) == 2
    assert "requires --apply" in capsys.readouterr().err


def test_cli_json_apply_requires_yes_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    plan = _plan(tmp_path, "claude-code")
    namespace = SimpleNamespace(
        client=["claude-code"],
        cwd=tmp_path / "project",
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "bridge-home",
        config_path=tmp_path / "bridge-config.toml",
        json=True,
        apply=True,
        yes=False,
        rollback=False,
    )
    monkeypatch.setattr(cli_module, "build_setup_plan", lambda **_kwargs: plan)

    assert cli_module._run_setup(namespace) == 2
    assert not _target(plan).exists()
    assert "requires --yes" in capsys.readouterr().err


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_safe_apply_path_fixtures_use_injected_temp_paths(tmp_path: Path, platform: str) -> None:
    plan = _plan(tmp_path, "claude-code", platform=platform)
    assert str(_target(plan)).startswith(str(tmp_path))
    assert not _target(plan).exists()


def test_atomic_replace_failure_preserves_existing_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    original = _write(target, {"mcpServers": {"other": {"command": "keep"}}})
    plan = _rebuild(tmp_path, "claude-code")
    monkeypatch.setattr(
        apply_module,
        "_publish_prepared_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("replace")),
    )

    result = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )

    assert result.clients[0].status == "failed"
    assert result.write_count == 0
    assert target.read_bytes() == original


def test_cli_yes_json_apply_emits_deterministic_counts_without_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    plan = _plan(tmp_path, "claude-code")
    namespace = SimpleNamespace(
        client=["claude-code"],
        cwd=tmp_path / "project",
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "bridge-home",
        config_path=tmp_path / "bridge-config.toml",
        json=True,
        apply=True,
        yes=True,
        rollback=False,
    )
    monkeypatch.setattr(cli_module, "build_setup_plan", lambda **_kwargs: plan)
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("--yes must not prompt"))

    assert cli_module._run_setup(namespace) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema_version"] == 1
    assert payload["write_count"] == 1
    assert payload["backup_count"] == 0
    assert payload["rollback_available"] is True
    assert payload["clients"][0]["status"] == "created"
    assert _target(plan).is_file()


def test_cli_human_confirmation_can_apply_only_after_yes_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    plan = _plan(tmp_path, "vscode")
    namespace = SimpleNamespace(
        client=["vscode"],
        cwd=tmp_path / "project",
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "bridge-home",
        config_path=tmp_path / "bridge-config.toml",
        json=False,
        apply=True,
        yes=False,
        rollback=False,
    )
    monkeypatch.setattr(cli_module, "build_setup_plan", lambda **_kwargs: plan)
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")

    assert cli_module._run_setup(namespace) == 0
    output = capsys.readouterr().out
    assert "Client: vscode" in output
    assert "Configuration files changed: 1" in output
    assert _target(plan).is_file()


def test_nonregular_config_target_fails_closed_without_receipt(tmp_path: Path) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    target.mkdir(parents=True)
    plan = _rebuild(tmp_path, "claude-code")

    snapshot = capture_setup_apply_snapshot(plan)
    result = apply_setup_plan(plan, current_plan=plan, snapshot=snapshot)

    assert result.clients[0].status == "skipped_manual_review"
    assert result.write_count == 0
    assert result.backup_count == 0
    assert target.is_dir()
    assert not (target.parent / f".{target.name}.amb-setup-receipt.json").exists()


def test_snapshot_read_failure_fails_closed_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(tmp_path, "claude-code")
    target = _target(plan)
    snapshot = capture_setup_apply_snapshot(plan)
    real_snapshot = apply_module._file_snapshot

    def unreadable(path: Path) -> object:
        if path == target:
            return apply_module.FileSnapshot(True, None, None, None, None, None, True, False)
        return real_snapshot(path)

    monkeypatch.setattr(apply_module, "_file_snapshot", unreadable)
    result = apply_setup_plan(plan, current_plan=_rebuild(tmp_path, "claude-code"), snapshot=snapshot)

    assert result.clients[0].status == "changed_since_plan"
    assert result.write_count == 0
    assert result.backup_count == 0
    assert not target.exists()
    assert not (target.parent / f".{target.name}.amb-setup-receipt.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX creation mode is the contract under test")
def test_backup_is_private_at_creation_with_exclusive_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stat

    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    _write(target, {"mcpServers": {"other": {"command": "keep"}}})
    plan = _rebuild(tmp_path, "claude-code")
    real_open = apply_module.os.open
    observed: list[tuple[str, int, int]] = []

    def observing_open(path: object, flags: int, mode: int = 0o777) -> int:
        if flags & os.O_CREAT:
            observed.append((str(path), flags, mode))
        return real_open(path, flags, mode)

    monkeypatch.setattr(apply_module.os, "open", observing_open)
    result = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )

    backup = Path(result.clients[0].backup_path or "")
    assert result.clients[0].status == "merged"
    assert observed
    backup_create = next(item for item in observed if item[0].endswith(".bak"))
    _, flags, mode = backup_create
    assert flags & os.O_EXCL
    assert mode == 0o600
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_confirmation_renderer_uses_actual_newlines(tmp_path: Path) -> None:
    plan = _plan(tmp_path, "claude-code")
    rendered = apply_module.render_setup_apply_confirmation(plan)

    assert "\n  Client:" in rendered
    assert "\\n" not in rendered


def test_receipt_is_durable_before_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    original = _write(target, {"mcpServers": {"other": {"command": "keep"}}})
    plan = _rebuild(tmp_path, "claude-code")
    observed: list[dict[str, object]] = []

    def inspect_receipt(_temporary: Path, destination: Path, **_kwargs: object) -> None:
        receipt = json.loads((destination.parent / f".{destination.name}.amb-setup-receipt.json").read_text())
        observed.append(receipt)
        assert destination.read_bytes() == original
        raise OSError("stop before publication")

    monkeypatch.setattr(apply_module, "_publish_prepared_bytes", inspect_receipt)
    result = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )

    assert result.clients[0].status == "failed"
    assert result.write_count == 0
    assert observed and observed[0]["before_digest"]
    assert target.read_bytes() == original
    assert not (target.parent / f".{target.name}.amb-setup-receipt.json").exists()


@pytest.mark.parametrize("client", ["claude-code", "vscode"])
def test_post_publication_durability_failure_retains_valid_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: str,
) -> None:
    if client == "claude-code":
        initial = _plan(tmp_path, client)
        target = _target(initial)
        original = _write(target, {"mcpServers": {"other": {"command": "keep"}}})
        plan = _rebuild(tmp_path, client)
    else:
        plan = _plan(tmp_path, client)
        target = _target(plan)
        original = None
    real_fsync = apply_module._fsync_replaced_path

    def fail_after_target_publication(path: Path) -> None:
        if path == target:
            raise OSError("durability after publication")
        real_fsync(path)

    monkeypatch.setattr(apply_module, "_fsync_replaced_path", fail_after_target_publication)
    result = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, client),
        snapshot=capture_setup_apply_snapshot(plan),
    )

    receipt_path = target.parent / f".{target.name}.amb-setup-receipt.json"
    assert result.clients[0].status == "failed"
    assert result.write_count == 1
    assert result.rollback_available is True
    assert target.is_file()
    assert receipt_path.is_file()

    monkeypatch.setattr(apply_module, "_fsync_replaced_path", real_fsync)
    rollback = rollback_setup_plan(_rebuild(tmp_path, client))
    assert rollback.write_count == 1
    if original is None:
        assert rollback.clients[0].status == "removed_created"
        assert not target.exists()
    else:
        assert rollback.clients[0].status == "restored"
        assert target.read_bytes() == original


def test_rollback_refuses_tampered_backup_bytes(tmp_path: Path) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    _write(target, {"mcpServers": {"other": {"command": "keep"}}})
    plan = _rebuild(tmp_path, "claude-code")
    applied = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )
    backup = Path(applied.clients[0].backup_path or "")
    before = target.read_bytes()
    backup.write_bytes(b"tampered backup")

    rollback = rollback_setup_plan(_rebuild(tmp_path, "claude-code"))

    assert rollback.clients[0].status == "skipped_manual_review"
    assert target.read_bytes() == before


def test_rollback_refuses_tampered_backup_path_without_reading_it(tmp_path: Path) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    _write(target, {"mcpServers": {"other": {"command": "keep"}}})
    plan = _rebuild(tmp_path, "claude-code")
    apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )
    receipt_path = target.parent / f".{target.name}.amb-setup-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    unrelated = tmp_path / "unrelated-backup.bak"
    unrelated.write_bytes(b"do not read me")
    receipt["backup_path"] = str(unrelated)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    before = target.read_bytes()

    rollback = rollback_setup_plan(_rebuild(tmp_path, "claude-code"))

    assert rollback.clients[0].status == "skipped_manual_review"
    assert target.read_bytes() == before
    assert unrelated.read_bytes() == b"do not read me"


def test_create_race_preserves_competitor_and_cleans_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(tmp_path, "vscode")
    target = _target(plan)
    competitor = b'{"servers": {"competitor": {}}}'

    monkeypatch.setattr(apply_module, "_before_publication", lambda path: path.write_bytes(competitor))
    result = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "vscode"),
        snapshot=capture_setup_apply_snapshot(plan),
    )

    assert result.clients[0].status == "changed_since_plan"
    assert result.write_count == 0
    assert target.read_bytes() == competitor
    assert not (target.parent / f".{target.name}.amb-setup-receipt.json").exists()
    assert not list(target.parent.glob(f".{target.name}.amb-*.tmp"))


def test_update_race_preserves_newer_target_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    _write(target, {"mcpServers": {"other": {"command": "original"}}})
    plan = _rebuild(tmp_path, "claude-code")
    newer = b'{"mcpServers": {"other": {"command": "newer"}}}'

    monkeypatch.setattr(apply_module, "_before_publication_revalidation", lambda path: path.write_bytes(newer))
    result = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )

    assert result.clients[0].status == "changed_since_plan"
    assert result.write_count == 0
    assert target.read_bytes() == newer
    assert not (target.parent / f".{target.name}.amb-setup-receipt.json").exists()
    assert not list(target.parent.glob(f".{target.name}.amb-*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="portable symlink permissions are not guaranteed on Windows runners")
def test_rollback_refuses_expected_backup_symlink(tmp_path: Path) -> None:
    initial = _plan(tmp_path, "claude-code")
    target = _target(initial)
    _write(target, {"mcpServers": {"other": {"command": "keep"}}})
    plan = _rebuild(tmp_path, "claude-code")
    applied = apply_setup_plan(
        plan,
        current_plan=_rebuild(tmp_path, "claude-code"),
        snapshot=capture_setup_apply_snapshot(plan),
    )
    backup = Path(applied.clients[0].backup_path or "")
    unrelated = tmp_path / "unrelated-bytes"
    unrelated.write_bytes(b"unrelated")
    backup.unlink()
    backup.symlink_to(unrelated)
    before = target.read_bytes()

    rollback = rollback_setup_plan(_rebuild(tmp_path, "claude-code"))

    assert rollback.clients[0].status == "skipped_manual_review"
    assert target.read_bytes() == before
    assert unrelated.read_bytes() == b"unrelated"
