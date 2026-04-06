from pathlib import Path

from agent_mem_bridge.paths import (
    resolve_bridge_db_path,
    resolve_bridge_home,
    resolve_profile_source_root,
    resolve_idle_seconds,
    resolve_poll_seconds,
    resolve_profile_namespace,
    resolve_sessions_root,
)


def test_path_resolvers_read_from_config_file(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[codex]",
                'home = "./codex-home"',
                "",
                "[profile]",
                'source_root = "./remote-cole"',
                'namespace = "global"',
                "",
                "[bridge]",
                'home = "./bridge-home"',
                'db_path = "custom.db"',
                "",
                "[watcher]",
                'sessions_root = "./sessions"',
                "idle_seconds = 45",
                "",
                "[service]",
                "poll_seconds = 12.5",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(config_path))
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_HOME", raising=False)
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_DB_PATH", raising=False)
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_PROFILE_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("COLE_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_SESSIONS_ROOT", raising=False)
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_IDLE_SECONDS", raising=False)
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_POLL_SECONDS", raising=False)

    assert resolve_profile_source_root() == tmp_path / "remote-cole"
    assert resolve_profile_namespace() == "global"
    assert resolve_bridge_home() == tmp_path / "bridge-home"
    assert resolve_bridge_db_path() == tmp_path / "bridge-home" / "custom.db"
    assert resolve_sessions_root() == tmp_path / "codex-home" / "sessions"
    assert resolve_idle_seconds() == 45
    assert resolve_poll_seconds() == 12.5


def test_env_overrides_config_values(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[bridge]",
                'home = "./bridge-home"',
                "",
                "[watcher]",
                "idle_seconds = 45",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(config_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path / "env-home"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_IDLE_SECONDS", "90")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_PROFILE_SOURCE_ROOT", str(tmp_path / "env-cole"))

    assert resolve_profile_source_root() == tmp_path / "env-cole"
    assert resolve_bridge_home() == tmp_path / "env-home"
    assert resolve_idle_seconds() == 90



