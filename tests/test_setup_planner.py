from __future__ import annotations

import json
from pathlib import Path

from agent_mem_bridge.cli import main
from agent_mem_bridge.setup_planner import _inspect_config, build_setup_plan, render_setup_plan


def _plan(
    tmp_path: Path,
    client: str,
    *,
    platform: str = "linux",
    detected: bool = False,
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return build_setup_plan(
        clients=[client],
        cwd=project,
        home=home,
        environ={},
        platform=platform,
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "missing-bridge-home",
        bridge_config_path=tmp_path / "missing-amb-config.toml",
        which=(lambda command: f"/usr/bin/{command}" if detected else None),
    )


def _client_plan(tmp_path: Path, client: str, **kwargs):
    return _plan(tmp_path, client, **kwargs).clients[0]


def _write_config(client_plan, content: str) -> Path:
    assert client_plan.config_path is not None
    path = Path(client_plan.config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _tree_snapshot(root: Path) -> dict[str, tuple[bool, bytes | None, int]]:
    snapshot: dict[str, tuple[bool, bytes | None, int]] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_file():
            snapshot[relative] = (True, path.read_bytes(), path.stat().st_mtime_ns)
        else:
            snapshot[relative] = (False, None, path.stat().st_mtime_ns)
    return snapshot


def test_setup_no_clients_detected_and_generic_is_not_auto_detected(tmp_path: Path) -> None:
    plan = build_setup_plan(
        cwd=tmp_path / "project",
        home=tmp_path / "home",
        environ={},
        platform="linux",
        which=lambda command: None,
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "bridge-home",
        bridge_config_path=tmp_path / "config.toml",
    )

    by_client = {client.client: client for client in plan.clients}
    assert by_client["codex"].detection_status == "not_detected"
    assert by_client["claude-code"].detection_status == "not_detected"
    assert by_client["vscode"].detection_status == "not_detected"
    assert by_client["opencode"].detection_status == "not_detected"
    assert by_client["generic"].detection_status == "path_unknown"
    assert plan.write_count == 0


def test_detected_client_with_no_config_has_create_preview(tmp_path: Path) -> None:
    client = _client_plan(tmp_path, "codex", detected=True)

    assert client.detection_status == "detected"
    assert client.existing_amb_state == "absent"
    assert client.recommended_action == "would_create"
    assert "mcp_servers.agentMemoryBridge" in client.proposed_fragment


def test_existing_json_without_amb_entry_is_bounded_and_secret_safe(tmp_path: Path) -> None:
    initial = _client_plan(tmp_path, "claude-code")
    _write_config(
        initial,
        json.dumps(
            {
                "mcpServers": {
                    "private-server": {
                        "command": "private-tool",
                        "env": {"API_KEY": "super-secret-token"},
                    }
                }
            }
        ),
    )

    plan = _plan(tmp_path, "claude-code")
    client = plan.clients[0]
    human = render_setup_plan(plan)

    assert client.existing_amb_state == "absent"
    assert client.recommended_action == "would_merge"
    assert client.unrelated_server_count == 1
    assert "super-secret-token" not in human
    assert "private-server" not in human
    assert "super-secret-token" not in json.dumps(plan.as_dict())
    assert "private-server" not in json.dumps(plan.as_dict())


def test_missing_json_server_container_is_absent(tmp_path: Path) -> None:
    initial = _client_plan(tmp_path, "claude-code")
    _write_config(initial, json.dumps({"other": {}}))

    client = _client_plan(tmp_path, "claude-code")

    assert client.existing_amb_state == "absent"
    assert client.recommended_action == "would_merge"


def test_wrong_json_server_container_shape_fails_closed(tmp_path: Path) -> None:
    initial = _client_plan(tmp_path, "claude-code")
    _write_config(initial, json.dumps({"mcpServers": []}))

    client = _client_plan(tmp_path, "claude-code")

    assert client.existing_amb_state == "unreadable"
    assert client.recommended_action == "manual_review"


def test_missing_toml_server_container_is_absent(tmp_path: Path) -> None:
    initial = _client_plan(tmp_path, "codex")
    _write_config(initial, 'profile = "local"\n')

    client = _client_plan(tmp_path, "codex")

    assert client.existing_amb_state == "absent"
    assert client.recommended_action == "would_merge"


def test_wrong_toml_server_container_shape_fails_closed(tmp_path: Path) -> None:
    initial = _client_plan(tmp_path, "codex")
    _write_config(initial, 'mcp_servers = "not-an-object"\n')

    client = _client_plan(tmp_path, "codex")

    assert client.existing_amb_state == "unreadable"
    assert client.recommended_action == "manual_review"


def test_existing_equivalent_json_entry_is_no_change(tmp_path: Path) -> None:
    initial = _client_plan(tmp_path, "claude-code")
    _write_config(initial, initial.proposed_fragment)

    client = _client_plan(tmp_path, "claude-code")

    assert client.existing_amb_state == "equivalent"
    assert client.recommended_action == "no_change"


def test_existing_differing_amb_entry_requires_update(tmp_path: Path) -> None:
    initial = _client_plan(tmp_path, "claude-code")
    payload = json.loads(initial.proposed_fragment)
    payload["mcpServers"]["agentMemoryBridge"]["command"] = "/old/amb/python"
    _write_config(initial, json.dumps(payload))

    client = _client_plan(tmp_path, "claude-code")

    assert client.existing_amb_state == "update_required"
    assert client.recommended_action == "would_update"


def test_conflicting_server_name_requires_manual_review(tmp_path: Path) -> None:
    initial = _client_plan(tmp_path, "claude-code")
    _write_config(
        initial,
        json.dumps({"mcpServers": {"agentMemoryBridge": {"command": "other-server"}}}),
    )

    client = _client_plan(tmp_path, "claude-code")

    assert client.existing_amb_state == "conflict"
    assert client.recommended_action == "manual_review"


def test_malformed_json_is_reported_without_body_leak(tmp_path: Path) -> None:
    initial = _client_plan(tmp_path, "claude-code")
    _write_config(initial, '{"mcpServers": {"private": "super-secret-token"')

    plan = _plan(tmp_path, "claude-code")
    client = plan.clients[0]

    assert client.existing_amb_state == "unreadable"
    assert client.recommended_action == "manual_review"
    assert "super-secret-token" not in render_setup_plan(plan)
    assert "super-secret-token" not in json.dumps(plan.as_dict())


def test_toml_inspection_classifies_equivalent_codex_entry(tmp_path: Path) -> None:
    initial = _client_plan(tmp_path, "codex")
    _write_config(initial, initial.proposed_fragment)

    client = _client_plan(tmp_path, "codex")

    assert client.config_format == "toml"
    assert client.existing_amb_state == "equivalent"
    assert client.recommended_action == "no_change"


def test_yaml_inspection_is_unavailable_without_runtime_parser(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("mcp_servers:\n  private: super-secret-token\n", encoding="utf-8")

    inspection = _inspect_config(
        path=path,
        config_format="yaml",
        client="hermes",
        proposed_fragment="mcp_servers:\n  agentMemoryBridge:\n",
    )

    assert inspection.state == "inspection_unavailable"
    assert inspection.unrelated_server_count is None
    assert "super-secret-token" not in (inspection.note or "")


def test_opencode_project_alternate_marker_requires_manual_review(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "opencode.jsonc").write_text('{"mcp": {}}', encoding="utf-8")

    plan = build_setup_plan(
        clients=["opencode"],
        cwd=project,
        home=tmp_path / "home",
        environ={},
        platform="linux",
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "missing-bridge-home",
        bridge_config_path=tmp_path / "missing-amb-config.toml",
        which=lambda command: None,
    )
    client = plan.clients[0]

    assert client.detection_status == "detected"
    assert client.existing_amb_state == "inspection_unavailable"
    assert client.recommended_action == "manual_review"


def test_opencode_custom_alternate_marker_requires_manual_review(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    custom_config = tmp_path / "custom-opencode.json"
    custom_config.write_text('{"mcp": {}}', encoding="utf-8")

    plan = build_setup_plan(
        clients=["opencode"],
        cwd=project,
        home=tmp_path / "home",
        environ={"OPENCODE_CONFIG": str(custom_config)},
        platform="linux",
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "missing-bridge-home",
        bridge_config_path=tmp_path / "missing-amb-config.toml",
        which=lambda command: None,
    )
    client = plan.clients[0]

    assert client.detection_status == "detected"
    assert client.existing_amb_state == "inspection_unavailable"
    assert client.recommended_action == "manual_review"


def test_opencode_custom_config_env_nonexistent_fails_closed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    custom_config = tmp_path / "custom" / "opencode.json"
    before = _tree_snapshot(tmp_path)

    plan = build_setup_plan(
        clients=["opencode"],
        cwd=project,
        home=tmp_path / "home",
        environ={"OPENCODE_CONFIG": str(custom_config)},
        platform="linux",
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "missing-bridge-home",
        bridge_config_path=tmp_path / "missing-amb-config.toml",
        which=lambda command: None,
    )
    client = plan.clients[0]

    assert client.detection_status == "detected"
    assert client.existing_amb_state == "inspection_unavailable"
    assert client.recommended_action == "manual_review"
    assert not custom_config.exists()
    assert not custom_config.parent.exists()
    assert _tree_snapshot(tmp_path) == before
    assert plan.write_count == 0


def test_opencode_custom_config_env_wins_over_default_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "home"
    default_config = home / ".config" / "opencode" / "opencode.json"
    default_config.parent.mkdir(parents=True)
    default_config.write_text('{"mcp": {}}', encoding="utf-8")
    custom_config = tmp_path / "custom" / "opencode.json"
    before = _tree_snapshot(tmp_path)

    plan = build_setup_plan(
        clients=["opencode"],
        cwd=project,
        home=home,
        environ={"OPENCODE_CONFIG": str(custom_config)},
        platform="linux",
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "missing-bridge-home",
        bridge_config_path=tmp_path / "missing-amb-config.toml",
        which=lambda command: None,
    )
    client = plan.clients[0]

    assert client.detection_status == "detected"
    assert client.existing_amb_state == "inspection_unavailable"
    assert client.recommended_action == "manual_review"
    assert not custom_config.exists()
    assert not custom_config.parent.exists()
    assert _tree_snapshot(tmp_path) == before
    assert plan.write_count == 0


def test_platform_path_fixtures_are_explicit(tmp_path: Path) -> None:
    linux_codex = _client_plan(tmp_path / "linux", "codex", platform="linux")
    mac_vscode = _client_plan(tmp_path / "mac", "vscode", platform="darwin")
    windows_claude = _client_plan(tmp_path / "windows", "claude-code", platform="win32")
    windows_opencode = _client_plan(tmp_path / "windows-opencode", "opencode", platform="win32")

    assert linux_codex.config_path is not None
    assert Path(linux_codex.config_path).name == "config.toml"
    assert Path(linux_codex.config_path).parent.name == ".codex"
    assert mac_vscode.config_path is not None
    assert Path(mac_vscode.config_path).name == "mcp.json"
    assert Path(mac_vscode.config_path).parent.name == ".vscode"
    assert windows_claude.config_path is not None
    assert Path(windows_claude.config_path).name == ".mcp.json"
    assert windows_opencode.detection_status == "path_unknown"
    assert windows_opencode.existing_amb_state == "inspection_unavailable"


def test_cli_explicit_client_selection_and_json_zero_writes(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()

    exit_code = main(
        [
            "setup",
            "--client",
            "generic",
            "--json",
            "--cwd",
            str(project),
            "--bridge-home",
            str(tmp_path / "missing-bridge-home"),
            "--config-path",
            str(tmp_path / "missing-config.toml"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema_version"] == 1
    assert payload["write_count"] == 0
    assert [client["client"] for client in payload["clients"]] == ["generic"]
    assert not (tmp_path / "missing-bridge-home").exists()
    assert not (tmp_path / "missing-config.toml").exists()


def test_cli_setup_help_has_no_mutation_switches(capsys) -> None:
    try:
        main(["setup", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    help_text = capsys.readouterr().out

    assert "--json" in help_text
    assert "--apply" not in help_text
    assert "--write" not in help_text
    assert "--force" not in help_text


def test_setup_preserves_existing_config_bytes_mtime_and_fake_home_tree(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir(parents=True)
    config = project / ".mcp.json"
    config.write_text(json.dumps({"mcpServers": {"private": {"token": "super-secret-token"}}}), encoding="utf-8")
    before = _tree_snapshot(tmp_path)
    before_bytes = config.read_bytes()
    before_mtime = config.stat().st_mtime_ns

    plan = build_setup_plan(
        clients=["claude-code", "codex", "vscode", "opencode", "generic"],
        cwd=project,
        home=home,
        environ={},
        platform="linux",
        which=lambda command: None,
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "missing-bridge-home",
        bridge_config_path=tmp_path / "missing-amb-config.toml",
    )
    after = _tree_snapshot(tmp_path)

    assert plan.write_count == 0
    assert before == after
    assert config.read_bytes() == before_bytes
    assert config.stat().st_mtime_ns == before_mtime
    assert not (tmp_path / "missing-bridge-home").exists()
    assert not (tmp_path / "missing-amb-config.toml").exists()


def test_duplicate_client_selection_is_deterministic(tmp_path: Path) -> None:
    plan = build_setup_plan(
        clients=["codex", "codex", "generic"],
        cwd=tmp_path / "project",
        home=tmp_path / "home",
        environ={},
        platform="linux",
        which=lambda command: None,
        python_path="/opt/amb/python",
        bridge_home=tmp_path / "bridge-home",
        bridge_config_path=tmp_path / "config.toml",
    )

    assert [client.client for client in plan.clients] == ["codex", "generic"]
    assert plan.as_dict()["write_count"] == 0
