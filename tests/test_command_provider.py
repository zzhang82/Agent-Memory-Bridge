from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path

import pytest

from agent_mem_bridge.command_provider import (
    CommandLimits,
    CommandProviderError,
    _command_argv,
    command_fingerprint,
    run_json_command,
)


def _embedding_fixture(mode: str) -> tuple[str, ...]:
    fixture = Path(__file__).parent / "fixtures" / "fake_embedding_gateway.py"
    return (sys.executable, str(fixture), mode)


def _environment_probe() -> tuple[str, ...]:
    code = (
        "import json,os,sys; json.load(sys.stdin); "
        "print(json.dumps({'secret': os.getenv('AMB_TEST_SECRET'), "
        "'path': bool(os.getenv('PATH'))}))"
    )
    return (sys.executable, "-c", code)


def test_argv_sequence_runs_without_shell() -> None:
    result = run_json_command(
        _embedding_fixture("ok"),
        {"texts": ["alpha"], "dim": 4},
        timeout_seconds=2,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["vectors"] == [[1.0, 0.0, 0.0, 0.0]]


def test_string_argv_parses_quoted_windows_paths_without_literal_quotes() -> None:
    command = '"C:\\Program Files\\Python\\python.exe" "C:\\path x\\gateway.py"'

    assert _command_argv(command, trusted_shell=False) == [
        "C:\\Program Files\\Python\\python.exe",
        "C:\\path x\\gateway.py",
    ]


def test_trusted_shell_requires_explicit_string_mode() -> None:
    with pytest.raises(CommandProviderError, match="trusted-shell"):
        run_json_command(
            _embedding_fixture("ok"),
            {},
            timeout_seconds=1,
            trusted_shell=True,
        )


@pytest.mark.skipif(os.name == "nt", reason="uses POSIX shell quoting")
def test_trusted_shell_string_runs_only_when_enabled() -> None:
    command = shlex.join(_embedding_fixture("ok"))

    result = run_json_command(
        command,
        {"texts": ["beta"], "dim": 4},
        timeout_seconds=2,
        trusted_shell=True,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["vectors"] == [[0.0, 1.0, 0.0, 0.0]]


@pytest.mark.parametrize(
    ("mode", "label"),
    [
        ("large-stdout", "stdout"),
        ("infinite-stdout", "stdout"),
        ("infinite-stderr", "stderr"),
    ],
)
def test_output_limits_kill_large_or_unbounded_provider(mode: str, label: str) -> None:
    with pytest.raises(CommandProviderError, match=rf"{label} exceeded"):
        run_json_command(
            _embedding_fixture(mode),
            {},
            timeout_seconds=2,
            limits=CommandLimits(
                max_input_bytes=1_000,
                max_stdout_bytes=32_768,
                max_stderr_bytes=32_768,
            ),
        )


def test_input_limit_is_checked_before_process_start() -> None:
    with pytest.raises(CommandProviderError, match="input exceeded"):
        run_json_command(
            ("definitely-not-a-real-command",),
            {"secret": "x" * 100},
            timeout_seconds=1,
            limits=CommandLimits(max_input_bytes=10, max_stdout_bytes=100, max_stderr_bytes=100),
        )


def test_environment_is_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMB_TEST_SECRET", "fixture-secret")

    default_result = run_json_command(_environment_probe(), {}, timeout_seconds=2)
    allowed_result = run_json_command(
        _environment_probe(),
        {},
        timeout_seconds=2,
        env_allowlist=("AMB_TEST_SECRET",),
    )

    assert json.loads(default_result.stdout) == {"secret": None, "path": True}
    assert json.loads(allowed_result.stdout) == {"secret": "fixture-secret", "path": True}


def test_command_fingerprint_is_stable_and_mode_sensitive() -> None:
    argv = (sys.executable, "gateway.py")

    assert command_fingerprint(argv) == command_fingerprint(argv)
    assert command_fingerprint(argv) != command_fingerprint("python gateway.py")
    assert command_fingerprint("python gateway.py") != command_fingerprint(
        "python gateway.py",
        trusted_shell=True,
    )


def test_timeout_and_limit_validation_are_explicit() -> None:
    with pytest.raises(ValueError, match="timeout"):
        run_json_command(_embedding_fixture("ok"), {}, timeout_seconds=0)
    with pytest.raises(ValueError, match="byte limits"):
        run_json_command(
            _embedding_fixture("ok"),
            {},
            timeout_seconds=1,
            limits=CommandLimits(max_input_bytes=0),
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group containment assertion")
def test_descendant_holding_output_pipes_is_killed_at_shared_timeout() -> None:
    started = time.monotonic()

    with pytest.raises(CommandProviderError, match="timed out while draining output"):
        run_json_command(
            _embedding_fixture("spawn-pipe-holder"),
            {"texts": ["alpha"], "dim": 4},
            timeout_seconds=0.5,
        )

    assert time.monotonic() - started < 2.0
