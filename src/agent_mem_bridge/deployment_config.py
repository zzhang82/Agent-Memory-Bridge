"""Explicit configuration for the optional Streamable HTTP deployment."""

from __future__ import annotations

import argparse
import ipaddress
import os
import stat
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000
_LOOPBACK_ALLOWED_HOSTS = (
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
    "[::1]",
    "[::1]:*",
)
_LOOPBACK_ALLOWED_ORIGINS = (
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
)


@dataclass(frozen=True, slots=True)
class HttpTransportConfig:
    """Network boundary for one optional AMB HTTP authority.

    The default keeps the authority on loopback. A non-loopback bind is allowed
    only with a dedicated static bearer token, enterprise gateway config, and explicit Host and Origin
    allowlists.
    """

    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    token_file: Path | None = None
    gateway_config_file: Path | None = None
    gateway_config_json: str | None = None
    auth_mode: str = "static"
    audit_stream: str = "stdout"

    @classmethod
    def from_env(cls) -> "HttpTransportConfig":
        """Load only explicit deployment environment variables."""

        config = cls._from_env()
        config.validate()
        return config

    @classmethod
    def _from_env(cls) -> "HttpTransportConfig":
        port_value = os.environ.get("AGENT_MEMORY_BRIDGE_HTTP_PORT", str(_DEFAULT_PORT))
        try:
            port = int(port_value)
        except ValueError as exc:
            raise ValueError("AGENT_MEMORY_BRIDGE_HTTP_PORT must be an integer") from exc
        token_file_value = os.environ.get("AGENT_MEMORY_BRIDGE_HTTP_TOKEN_FILE")
        gateway_config_file_value = os.environ.get(
            "AGENT_MEMORY_BRIDGE_GATEWAY_CONFIG_FILE",
            os.environ.get("AMB_GATEWAY_CONFIG_FILE"),
        )
        gateway_config_json = os.environ.get(
            "AGENT_MEMORY_BRIDGE_GATEWAY_CONFIG",
            os.environ.get("AMB_GATEWAY_CONFIG"),
        )
        auth_mode = os.environ.get(
            "AGENT_MEMORY_BRIDGE_AUTH_MODE",
            os.environ.get("AMB_AUTH_MODE", "rbac" if (gateway_config_file_value or gateway_config_json) else "static"),
        )
        audit_stream = os.environ.get(
            "AGENT_MEMORY_BRIDGE_AUDIT_STREAM",
            os.environ.get("AMB_AUDIT_STREAM", "stdout"),
        )
        return cls(
            host=os.environ.get("AGENT_MEMORY_BRIDGE_HTTP_HOST", _DEFAULT_HOST).strip(),
            port=port,
            allowed_hosts=_split_csv_env("AGENT_MEMORY_BRIDGE_HTTP_ALLOWED_HOSTS"),
            allowed_origins=_split_csv_env("AGENT_MEMORY_BRIDGE_HTTP_ALLOWED_ORIGINS"),
            token_file=Path(token_file_value).expanduser() if token_file_value else None,
            gateway_config_file=Path(gateway_config_file_value).expanduser() if gateway_config_file_value else None,
            gateway_config_json=gateway_config_json,
            auth_mode=auth_mode,
            audit_stream=audit_stream,
        )

    @property
    def is_loopback(self) -> bool:
        return _is_loopback_host(self.host)

    @property
    def is_authenticated(self) -> bool:
        return self.token_file is not None or self.gateway_config_file is not None or bool(self.gateway_config_json)

    @property
    def effective_allowed_hosts(self) -> tuple[str, ...]:
        if self.allowed_hosts:
            return self.allowed_hosts
        return _LOOPBACK_ALLOWED_HOSTS if self.is_loopback else ()

    @property
    def effective_allowed_origins(self) -> tuple[str, ...]:
        if self.allowed_origins:
            return self.allowed_origins
        return _LOOPBACK_ALLOWED_ORIGINS if self.is_loopback else ()

    def validate(self) -> None:
        if not self.host or any(char.isspace() or ord(char) < 32 for char in self.host):
            raise ValueError("HTTP host must be a non-empty hostname or IP address")
        if not 0 < self.port < 65536:
            raise ValueError("HTTP port must be between 1 and 65535")
        _validate_allowed_hosts(self.allowed_hosts)
        _validate_allowed_origins(self.allowed_origins)
        if not self.is_loopback:
            if not self.is_authenticated:
                raise ValueError("non-loopback HTTP bind requires a bearer token file or gateway config")
            if not self.allowed_hosts:
                raise ValueError("non-loopback HTTP bind requires explicit allowed hosts")
            if not self.allowed_origins:
                raise ValueError("non-loopback HTTP bind requires explicit allowed origins")

    def as_report(self) -> dict[str, object]:
        """Return operator-useful exposure facts without paths or token values."""

        return {
            "transport": "streamable-http",
            "host": self.host,
            "port": self.port,
            "loopback": self.is_loopback,
            "allowed_hosts": list(self.effective_allowed_hosts),
            "allowed_origins": list(self.effective_allowed_origins),
            "token_configured": self.token_file is not None,
            "gateway_configured": self.gateway_config_file is not None or bool(self.gateway_config_json),
            "auth_mode": self.auth_mode,
        }


def add_http_arguments(parser: argparse.ArgumentParser) -> None:
    """Add shared HTTP deployment flags without duplicating CLI parsing logic."""

    parser.add_argument("--host", default=None, help="HTTP bind host; defaults to loopback.")
    parser.add_argument("--port", type=int, default=None, help="HTTP bind port; defaults to 8000.")
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=None,
        help="Allowed Host header for the MCP transport; repeat for more than one.",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=None,
        help="Allowed Origin header for the MCP transport; repeat for more than one.",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=None,
        help="Dedicated private-deployment bearer token file; its contents are never logged.",
    )
    parser.add_argument(
        "--gateway-config",
        type=Path,
        default=None,
        help="Path to AMB Enterprise Gateway tokens and RBAC configuration file.",
    )
    parser.add_argument(
        "--auth-mode",
        choices=["static", "rbac"],
        default=None,
        help="Authentication mode ('static' or 'rbac').",
    )
    parser.add_argument(
        "--audit-stream",
        default=None,
        help="Target for structured JSONL audit stream ('stdout', 'stderr', or file path).",
    )


def config_from_namespace(namespace: argparse.Namespace) -> HttpTransportConfig:
    """Combine optional HTTP CLI flags with the explicit environment defaults."""

    defaults = HttpTransportConfig._from_env()
    configured_host = getattr(namespace, "host", None)
    configured_port = getattr(namespace, "port", None)
    configured_gateway_config = getattr(namespace, "gateway_config", None)
    configured_auth_mode = getattr(namespace, "auth_mode", None)
    configured_audit_stream = getattr(namespace, "audit_stream", None)
    config = HttpTransportConfig(
        host=defaults.host if configured_host is None else str(configured_host),
        port=defaults.port if configured_port is None else int(configured_port),
        allowed_hosts=tuple(getattr(namespace, "allowed_host", None) or defaults.allowed_hosts),
        allowed_origins=tuple(getattr(namespace, "allowed_origin", None) or defaults.allowed_origins),
        token_file=getattr(namespace, "token_file", None) or defaults.token_file,
        gateway_config_file=configured_gateway_config or defaults.gateway_config_file,
        gateway_config_json=defaults.gateway_config_json,
        auth_mode=configured_auth_mode
        or ("rbac" if (configured_gateway_config or defaults.gateway_config_file) else defaults.auth_mode),
        audit_stream=configured_audit_stream or defaults.audit_stream,
    )
    config.validate()
    return config


def config_from_env() -> HttpTransportConfig:
    """Compatibility-shaped explicit name for doctor and verify call sites."""

    return HttpTransportConfig.from_env()


def read_token_file(path: Path) -> str:
    """Read one private bearer token without exposing its contents in errors."""

    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError("token file must be a regular file")
        if os.name == "posix" and metadata.st_mode & 0o077:
            raise OSError("token file must not be group or world accessible")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
                or (os.name == "posix" and opened.st_mode & 0o077)
            ):
                raise OSError("opened token file is not the expected private regular file")
            raw_token = handle.read(8193)
    except OSError as exc:
        raise ValueError("unable to read HTTP bearer token file") from exc
    token = raw_token[:-2] if raw_token.endswith(b"\r\n") else raw_token.removesuffix(b"\n")
    if not token or len(raw_token) > 8192 or len(token) > 8192 or any(byte < 33 or byte > 126 for byte in token):
        raise ValueError("HTTP bearer token file must contain one non-empty token")
    return token.decode("ascii")


def _split_csv_env(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]")
    if normalized.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_allowed_hosts(values: tuple[str, ...]) -> None:
    for value in values:
        wildcard = value.endswith(":*")
        base = value[:-2] if wildcard else value
        if (
            not base
            or any(char.isspace() or ord(char) < 33 for char in value)
            or any(char in base for char in "/?#@")
            or "*" in base
            or ("*" in value and not wildcard)
        ):
            raise ValueError("HTTP allowed host entries must be exact hosts or an exact host with :*")


def _validate_allowed_origins(values: tuple[str, ...]) -> None:
    for value in values:
        scheme, separator, authority = value.partition("://")
        if (
            scheme not in {"http", "https"}
            or not separator
            or not authority
            or any(char.isspace() or ord(char) < 33 for char in value)
            or any(char in authority for char in "/?#@")
        ):
            raise ValueError("HTTP allowed origin entries must be exact HTTP(S) origins")
        try:
            _validate_allowed_hosts((authority,))
        except ValueError as exc:
            raise ValueError("HTTP allowed origin entries must be exact HTTP(S) origins") from exc
