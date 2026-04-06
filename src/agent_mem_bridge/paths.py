from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Callable


def _first_env(*names: str) -> str | None:
    for name in names:
        raw = os.environ.get(name)
        if raw:
            return raw
    return None


def _default_codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".codex"


def _default_bridge_home() -> Path:
    raw = _first_env("AGENT_MEMORY_BRIDGE_HOME")
    if raw:
        return Path(raw).expanduser()
    return _default_codex_home() / "mem-bridge"


def resolve_config_path() -> Path:
    raw = _first_env("AGENT_MEMORY_BRIDGE_CONFIG")
    if raw:
        return Path(raw).expanduser()
    return _default_bridge_home() / "config.toml"


def _load_config() -> dict[str, Any]:
    config_path = resolve_config_path()
    if not config_path.is_file():
        return {}
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    return data if isinstance(data, dict) else {}


def _config_value(*keys: str) -> Any:
    value: Any = _load_config()
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _resolve_config_path_value(value: str, base_dir: Path | None = None) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    if base_dir is not None:
        return base_dir / candidate
    return resolve_config_path().parent / candidate


def _resolve_path(
    env_names: str | tuple[str, ...],
    config_keys: tuple[str, ...],
    default_factory: Callable[[], Path],
    config_base_factory: Callable[[], Path] | None = None,
) -> Path:
    names = (env_names,) if isinstance(env_names, str) else env_names
    raw = _first_env(*names)
    if raw:
        return Path(raw).expanduser()
    configured = _config_value(*config_keys)
    if isinstance(configured, str) and configured.strip():
        base_dir = config_base_factory() if config_base_factory is not None else None
        return _resolve_config_path_value(configured.strip(), base_dir=base_dir)
    return default_factory()


def _resolve_int(env_names: str | tuple[str, ...], config_keys: tuple[str, ...], default: int) -> int:
    names = (env_names,) if isinstance(env_names, str) else env_names
    raw = _first_env(*names)
    if raw:
        return int(raw)
    configured = _config_value(*config_keys)
    if configured is None:
        return default
    return int(configured)


def _resolve_float(env_names: str | tuple[str, ...], config_keys: tuple[str, ...], default: float) -> float:
    names = (env_names,) if isinstance(env_names, str) else env_names
    raw = _first_env(*names)
    if raw:
        return float(raw)
    configured = _config_value(*config_keys)
    if configured is None:
        return default
    return float(configured)


def _resolve_str(env_names: str | tuple[str, ...], config_keys: tuple[str, ...], default: str) -> str:
    names = (env_names,) if isinstance(env_names, str) else env_names
    raw = _first_env(*names)
    if raw:
        return raw.strip()
    configured = _config_value(*config_keys)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return default


def resolve_codex_home() -> Path:
    return _resolve_path("CODEX_HOME", ("codex", "home"), _default_codex_home)


def resolve_bridge_home() -> Path:
    return _resolve_path("AGENT_MEMORY_BRIDGE_HOME", ("bridge", "home"), _default_bridge_home)


def resolve_profile_source_root() -> Path:
    raw = _first_env("AGENT_MEMORY_BRIDGE_PROFILE_SOURCE_ROOT", "COLE_SOURCE_ROOT")
    if raw:
        return Path(raw).expanduser()

    configured = _config_value("profile", "source_root")
    if not isinstance(configured, str) or not configured.strip():
        configured = _config_value("cole", "source_root")
    if isinstance(configured, str) and configured.strip():
        return _resolve_config_path_value(configured.strip())

    return Path(__file__).resolve().parents[3] / "Cole"


def resolve_cole_source_root() -> Path:
    return resolve_profile_source_root()


def resolve_profile_namespace() -> str:
    return _resolve_str("AGENT_MEMORY_BRIDGE_PROFILE_NAMESPACE", ("profile", "namespace"), "global")


def resolve_reflex_actor() -> str:
    return _resolve_str("AGENT_MEMORY_BRIDGE_REFLEX_ACTOR", ("profile", "reflex_actor"), "bridge-reflex")


def resolve_consolidation_actor() -> str:
    return _resolve_str(
        "AGENT_MEMORY_BRIDGE_CONSOLIDATION_ACTOR",
        ("profile", "consolidation_actor"),
        "bridge-consolidation",
    )


def resolve_learn_title_prefix() -> str:
    return _resolve_str("AGENT_MEMORY_BRIDGE_LEARN_TITLE_PREFIX", ("profile", "learn_title_prefix"), "[[Learn]]")


def resolve_domain_title_prefix() -> str:
    return _resolve_str(
        "AGENT_MEMORY_BRIDGE_DOMAIN_TITLE_PREFIX",
        ("profile", "domain_title_prefix"),
        "[[Domain Note]]",
    )


def resolve_bridge_db_path() -> Path:
    return _resolve_path(
        "AGENT_MEMORY_BRIDGE_DB_PATH",
        ("bridge", "db_path"),
        lambda: resolve_bridge_home() / "bridge.db",
        config_base_factory=resolve_bridge_home,
    )


def resolve_bridge_log_dir() -> Path:
    return _resolve_path(
        "AGENT_MEMORY_BRIDGE_LOG_DIR",
        ("bridge", "log_dir"),
        lambda: resolve_bridge_home() / "logs",
        config_base_factory=resolve_bridge_home,
    )


def resolve_watcher_state_path() -> Path:
    return _resolve_path(
        "AGENT_MEMORY_BRIDGE_STATE_PATH",
        ("watcher", "state_path"),
        lambda: resolve_bridge_home() / "watcher-state.json",
        config_base_factory=resolve_bridge_home,
    )


def resolve_watcher_notes_root() -> Path:
    return _resolve_path(
        "AGENT_MEMORY_BRIDGE_NOTES_ROOT",
        ("watcher", "notes_root"),
        lambda: resolve_bridge_home() / "session-notes" / "auto",
        config_base_factory=resolve_bridge_home,
    )


def resolve_watcher_log_dir() -> Path:
    return _resolve_path(
        "AGENT_MEMORY_BRIDGE_WATCHER_LOG_DIR",
        ("watcher", "log_dir"),
        lambda: resolve_bridge_home() / "watcher-logs",
        config_base_factory=resolve_bridge_home,
    )


def resolve_reflex_state_path() -> Path:
    return _resolve_path(
        "AGENT_MEMORY_BRIDGE_REFLEX_STATE_PATH",
        ("reflex", "state_path"),
        lambda: resolve_bridge_home() / "reflex-state.json",
        config_base_factory=resolve_bridge_home,
    )


def resolve_consolidation_state_path() -> Path:
    return _resolve_path(
        "AGENT_MEMORY_BRIDGE_CONSOLIDATION_STATE_PATH",
        ("consolidation", "state_path"),
        lambda: resolve_bridge_home() / "consolidation-state.json",
        config_base_factory=resolve_bridge_home,
    )


def resolve_sessions_root() -> Path:
    return _resolve_path(
        "AGENT_MEMORY_BRIDGE_SESSIONS_ROOT",
        ("watcher", "sessions_root"),
        lambda: resolve_codex_home() / "sessions",
        config_base_factory=resolve_codex_home,
    )


def resolve_idle_seconds() -> int:
    return _resolve_int("AGENT_MEMORY_BRIDGE_IDLE_SECONDS", ("watcher", "idle_seconds"), 60)


def resolve_checkpoint_seconds() -> int:
    return _resolve_int(
        "AGENT_MEMORY_BRIDGE_CHECKPOINT_SECONDS",
        ("watcher", "checkpoint_seconds"),
        300,
    )


def resolve_checkpoint_min_messages() -> int:
    return _resolve_int(
        "AGENT_MEMORY_BRIDGE_CHECKPOINT_MIN_MESSAGES",
        ("watcher", "checkpoint_min_messages"),
        2,
    )


def resolve_reflex_scan_limit() -> int:
    return _resolve_int("AGENT_MEMORY_BRIDGE_REFLEX_SCAN_LIMIT", ("reflex", "scan_limit"), 200)


def resolve_consolidation_scan_limit() -> int:
    return _resolve_int(
        "AGENT_MEMORY_BRIDGE_CONSOLIDATION_SCAN_LIMIT",
        ("consolidation", "scan_limit"),
        200,
    )


def resolve_poll_seconds() -> float:
    return _resolve_float("AGENT_MEMORY_BRIDGE_POLL_SECONDS", ("service", "poll_seconds"), 30.0)
