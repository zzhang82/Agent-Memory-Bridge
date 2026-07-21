from pathlib import Path

import pytest

from agent_mem_bridge.paths import (
    resolve_bridge_db_path,
    resolve_bridge_home,
    resolve_classifier_trusted_shell,
    resolve_consolidation_allow_reflex_sources,
    resolve_consolidation_enabled,
    resolve_embedding_command,
    resolve_embedding_dim,
    resolve_embedding_model,
    resolve_embedding_provider,
    resolve_embedding_scheduler_batch_size,
    resolve_embedding_scheduler_enabled,
    resolve_embedding_scheduler_interval_seconds,
    resolve_embedding_scheduler_state_path,
    resolve_embedding_timeout_seconds,
    resolve_embedding_trusted_shell,
    resolve_idle_seconds,
    resolve_operating_profile,
    resolve_poll_seconds,
    resolve_profile_namespace,
    resolve_profile_source_root,
    resolve_reflex_enabled,
    resolve_require_claim_before_ack,
    resolve_sessions_root,
    resolve_telemetry_log_dir,
    resolve_telemetry_mode,
    resolve_telemetry_service_name,
    resolve_watcher_enabled,
)


def test_hardened_local_profile_enforces_strict_signal_and_command_boundaries(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_OPERATING_PROFILE", "hardened-local")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_TRUSTED_SHELL", "true")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CLASSIFIER_TRUSTED_SHELL", "true")

    assert resolve_operating_profile() == "hardened-local"
    assert resolve_require_claim_before_ack() is True
    with pytest.raises(ValueError, match="embedding_trusted_shell"):
        resolve_embedding_trusted_shell()
    with pytest.raises(ValueError, match="classifier trusted_shell"):
        resolve_classifier_trusted_shell()


def test_invalid_operating_profile_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_OPERATING_PROFILE", "distributed-production")

    with pytest.raises(ValueError, match="operating_profile"):
        resolve_operating_profile()


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
                "[telemetry]",
                'mode = "jsonl"',
                'log_dir = "telemetry-spans"',
                'service_name = "amb-local"',
                "",
                "[retrieval]",
                'embedding_provider = "command"',
                'embedding_command = "python fake_embedding.py"',
                'embedding_model = "fixture-embedding-v1"',
                "embedding_dim = 4",
                "embedding_timeout_seconds = 3.5",
                "",
                "[embedding_scheduler]",
                "enabled = true",
                'state_path = "embedding-sidecar-state.json"',
                "interval_seconds = 42.5",
                "batch_size = 7",
                "",
                "[consolidation]",
                "enabled = true",
                "allow_reflex_sources = true",
                "",
                "[watcher]",
                "enabled = true",
                'sessions_root = "./sessions"',
                "idle_seconds = 45",
                "",
                "[service]",
                "poll_seconds = 12.5",
                "",
                "[reflex]",
                "enabled = true",
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
    assert resolve_telemetry_mode() == "jsonl"
    assert resolve_telemetry_log_dir() == tmp_path / "bridge-home" / "telemetry-spans"
    assert resolve_telemetry_service_name() == "amb-local"
    assert resolve_embedding_provider() == "command"
    assert resolve_embedding_command() == "python fake_embedding.py"
    assert resolve_embedding_model() == "fixture-embedding-v1"
    assert resolve_embedding_dim() == 4
    assert resolve_embedding_timeout_seconds() == 3.5
    assert resolve_embedding_scheduler_enabled() is True
    assert resolve_embedding_scheduler_state_path() == tmp_path / "bridge-home" / "embedding-sidecar-state.json"
    assert resolve_embedding_scheduler_interval_seconds() == 42.5
    assert resolve_embedding_scheduler_batch_size() == 7
    assert resolve_consolidation_enabled() is True
    assert resolve_consolidation_allow_reflex_sources() is True
    assert resolve_watcher_enabled() is True
    assert resolve_sessions_root() == tmp_path / "codex-home" / "sessions"
    assert resolve_reflex_enabled() is True
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
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_TELEMETRY_MODE", "jsonl")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_TELEMETRY_SERVICE_NAME", "amb-env")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_PROVIDER", "hash")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_MODEL", "env-embedding")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_DIM", "8")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_SCHEDULER_ENABLED", "yes")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_SCHEDULER_INTERVAL_SECONDS", "25")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_SCHEDULER_BATCH_SIZE", "3")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_WATCHER_ENABLED", "yes")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_REFLEX_ENABLED", "yes")

    assert resolve_profile_source_root() == tmp_path / "env-cole"
    assert resolve_bridge_home() == tmp_path / "env-home"
    assert resolve_idle_seconds() == 90
    assert resolve_telemetry_mode() == "jsonl"
    assert resolve_telemetry_service_name() == "amb-env"
    assert resolve_embedding_provider() == "hash"
    assert resolve_embedding_model() == "env-embedding"
    assert resolve_embedding_dim() == 8
    assert resolve_embedding_scheduler_enabled() is True
    assert resolve_embedding_scheduler_state_path() == tmp_path / "env-home" / "embedding-sidecar-state.json"
    assert resolve_embedding_scheduler_interval_seconds() == 25
    assert resolve_embedding_scheduler_batch_size() == 3
    assert resolve_watcher_enabled() is True
    assert resolve_reflex_enabled() is True


def test_service_automation_defaults_to_conservative_disabled(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[bridge]\nhome = './bridge-home'\n", encoding="utf-8")

    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(config_path))
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_WATCHER_ENABLED", raising=False)
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_REFLEX_ENABLED", raising=False)

    assert resolve_watcher_enabled() is False
    assert resolve_reflex_enabled() is False


def test_profile_source_root_defaults_to_neutral_config_path(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(config_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path / "bridge-home"))
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_PROFILE_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("COLE_SOURCE_ROOT", raising=False)

    assert resolve_profile_source_root() == Path.home() / ".config" / "agent-memory-bridge" / "profile-source"
