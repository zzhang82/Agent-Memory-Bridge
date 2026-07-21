from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_ENV_ALLOWLIST = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
)


class CommandProviderError(RuntimeError):
    """Sanitized failure raised by a bounded local command provider."""


@dataclass(frozen=True, slots=True)
class CommandLimits:
    max_input_bytes: int = 1_000_000
    max_stdout_bytes: int = 4_000_000
    max_stderr_bytes: int = 65_536


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    fingerprint: str


def run_json_command(
    command: str | Sequence[str],
    payload: Mapping[str, Any],
    *,
    timeout_seconds: float,
    trusted_shell: bool = False,
    limits: CommandLimits | None = None,
    env_allowlist: Sequence[str] = (),
) -> CommandResult:
    if timeout_seconds <= 0:
        raise ValueError("command provider timeout must be greater than 0")
    resolved_limits = limits or CommandLimits()
    _validate_limits(resolved_limits)
    stdin_bytes = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(stdin_bytes) > resolved_limits.max_input_bytes:
        raise CommandProviderError("command provider input exceeded the configured byte limit")
    argv = _command_argv(command, trusted_shell=trusted_shell)
    fingerprint = command_fingerprint(command, trusted_shell=trusted_shell)
    environment = _sanitized_environment(env_allowlist)
    process_group_options: dict[str, Any] = {}
    if os.name == "posix":
        process_group_options["start_new_session"] = True
    elif os.name == "nt":
        process_group_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = subprocess.Popen(
            command if trusted_shell else argv,
            shell=trusted_shell,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            **process_group_options,
        )
    except OSError as exc:
        raise CommandProviderError(
            f"command provider failed to start ({exc.__class__.__name__}; fingerprint={fingerprint})"
        ) from exc

    stdout = bytearray()
    stderr = bytearray()
    overflow: list[str] = []
    thread_errors: list[BaseException] = []

    def read_stream(stream: Any, target: bytearray, limit: int, label: str) -> None:
        try:
            while True:
                chunk = stream.read(65_536)
                if not chunk:
                    return
                if len(target) + len(chunk) > limit:
                    overflow.append(label)
                    _terminate_process_tree(process)
                    return
                target.extend(chunk)
        except BaseException as exc:  # pragma: no cover - surfaced below
            thread_errors.append(exc)

    def write_stdin() -> None:
        try:
            assert process.stdin is not None
            process.stdin.write(stdin_bytes)
            process.stdin.close()
        except BrokenPipeError:
            return
        except BaseException as exc:  # pragma: no cover - surfaced below
            thread_errors.append(exc)

    assert process.stdout is not None
    assert process.stderr is not None
    threads = [
        threading.Thread(
            target=read_stream,
            args=(process.stdout, stdout, resolved_limits.max_stdout_bytes, "stdout"),
            daemon=True,
        ),
        threading.Thread(
            target=read_stream,
            args=(process.stderr, stderr, resolved_limits.max_stderr_bytes, "stderr"),
            daemon=True,
        ),
        threading.Thread(target=write_stdin, daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout_seconds
    try:
        returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        _close_process_streams(process)
        _join_threads(threads, deadline=time.monotonic() + 1.0)
        raise CommandProviderError(f"command provider timed out (fingerprint={fingerprint})") from exc
    _join_threads(threads, deadline=deadline)
    if any(thread.is_alive() for thread in threads):
        _terminate_process_tree(process)
        _close_process_streams(process)
        _join_threads(threads, deadline=time.monotonic() + 1.0)
        raise CommandProviderError(f"command provider timed out while draining output (fingerprint={fingerprint})")
    _terminate_process_tree(process)
    if overflow:
        raise CommandProviderError(
            f"command provider {overflow[0]} exceeded the configured byte limit (fingerprint={fingerprint})"
        )
    if thread_errors:
        error = thread_errors[0]
        raise CommandProviderError(
            f"command provider I/O failed ({error.__class__.__name__}; fingerprint={fingerprint})"
        ) from error
    try:
        stdout_text = bytes(stdout).decode("utf-8", errors="strict")
        stderr_text = bytes(stderr).decode("utf-8", errors="replace")
    except UnicodeDecodeError as exc:
        raise CommandProviderError(f"command provider output was not valid UTF-8 (fingerprint={fingerprint})") from exc
    return CommandResult(
        returncode=returncode,
        stdout=stdout_text,
        stderr=stderr_text,
        fingerprint=fingerprint,
    )


def command_fingerprint(command: str | Sequence[str], *, trusted_shell: bool = False) -> str:
    if isinstance(command, str):
        normalized = command.strip()
    else:
        normalized = json.dumps([str(part) for part in command], ensure_ascii=True, separators=(",", ":"))
    mode = "trusted-shell" if trusted_shell else "argv"
    digest = hashlib.sha256(f"{mode}:{normalized}".encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def _command_argv(command: str | Sequence[str], *, trusted_shell: bool) -> list[str]:
    if trusted_shell:
        if not isinstance(command, str) or not command.strip():
            raise CommandProviderError("trusted-shell command provider requires a non-empty string command")
        return []
    if isinstance(command, str):
        try:
            # POSIX-style splitting also handles quoted Windows paths without
            # retaining the quote characters as part of argv. TOML arrays are
            # still the preferred cross-platform form because they need no
            # command-line parsing at all.
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            raise CommandProviderError("command provider argv could not be parsed") from exc
    else:
        argv = [str(part) for part in command]
    if not argv or any(not part for part in argv):
        raise CommandProviderError("command provider requires a non-empty argv")
    return argv


def _sanitized_environment(extra_allowlist: Sequence[str]) -> dict[str, str]:
    allowed = {*DEFAULT_ENV_ALLOWLIST, *(str(name).strip() for name in extra_allowlist if str(name).strip())}
    return {name: value for name in sorted(allowed) if (value := os.environ.get(name)) is not None}


def _validate_limits(limits: CommandLimits) -> None:
    if limits.max_input_bytes <= 0 or limits.max_stdout_bytes <= 0 or limits.max_stderr_bytes <= 0:
        raise ValueError("command provider byte limits must be greater than 0")


def _join_threads(threads: Sequence[threading.Thread], *, deadline: float) -> None:
    for thread in threads:
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining)


def _close_process_streams(process: subprocess.Popen[Any]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
