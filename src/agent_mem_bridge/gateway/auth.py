"""Authentication, principal identity model, and token verification for AMB Gateway."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.auth.middleware.bearer_auth import AccessToken

from .permissions import ALL_ROLES


def hash_token(token: str) -> str:
    """Compute the deterministic SHA-256 token hash formatted with 'sha256:' prefix."""
    cleaned = token.strip()
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True, slots=True)
class Principal:
    """One authenticated enterprise actor with bound role and namespace enclaves."""

    principal_id: str
    token_hash: str
    role: str
    allowed_namespaces: tuple[str, ...] = ("*",)
    rate_limit_rpm: int = 0

    def validate(self) -> None:
        if not self.principal_id or not self.principal_id.strip():
            raise ValueError("principal_id must be a non-empty string")
        if not self.token_hash.startswith("sha256:") or len(self.token_hash) != 71:
            raise ValueError(
                f"token_hash for '{self.principal_id}' must be a valid sha256 hex digest ('sha256:<64 hex>')"
            )
        if self.role not in ALL_ROLES:
            raise ValueError(
                f"role '{self.role}' for '{self.principal_id}' is invalid; must be one of {sorted(ALL_ROLES)}"
            )
        if not self.allowed_namespaces:
            raise ValueError(f"allowed_namespaces for '{self.principal_id}' cannot be empty")
        if self.rate_limit_rpm < 0:
            raise ValueError(f"rate_limit_rpm for '{self.principal_id}' must be non-negative")


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """Enterprise Gateway configuration containing all principals and policy parameters."""

    principals: tuple[Principal, ...]
    auth_mode: str = "rbac"
    audit_stream: str = "stdout"
    require_auth: bool = True

    def validate(self) -> None:
        if self.auth_mode not in ("rbac", "static"):
            raise ValueError(f"auth_mode must be 'rbac' or 'static', got '{self.auth_mode}'")
        if self.require_auth and not self.principals:
            raise ValueError("GatewayConfig requires at least one principal when require_auth is True")
        seen_ids: set[str] = set()
        seen_hashes: set[str] = set()
        for principal in self.principals:
            principal.validate()
            if principal.principal_id in seen_ids:
                raise ValueError(f"Duplicate principal_id: '{principal.principal_id}'")
            if principal.token_hash in seen_hashes:
                raise ValueError(f"Duplicate token_hash detected for principal '{principal.principal_id}'")
            seen_ids.add(principal.principal_id)
            seen_hashes.add(principal.token_hash)


def load_gateway_config(source: str | Path | dict[str, Any]) -> GatewayConfig:
    """Load and validate GatewayConfig from a JSON file path, JSON string, or dict."""
    raw: dict[str, Any]
    if isinstance(source, Path):
        raw = json.loads(source.read_text(encoding="utf-8"))
    elif isinstance(source, str):
        stripped = source.strip()
        if stripped.startswith("{") or "\n" in stripped:
            raw = json.loads(stripped)
        else:
            candidate_path = Path(stripped).expanduser()
            if candidate_path.is_file():
                raw = json.loads(candidate_path.read_text(encoding="utf-8"))
            else:
                raw = json.loads(stripped)
    elif isinstance(source, dict):
        raw = source
    else:
        raise TypeError(f"Expected path, JSON string, or dict, got {type(source).__name__}")

    if not isinstance(raw, dict):
        raise ValueError("Gateway config must be a JSON object")

    principals_raw = raw.get("principals", [])
    if not isinstance(principals_raw, list):
        raise ValueError("'principals' in gateway config must be a list")

    principals: list[Principal] = []
    for item in principals_raw:
        if not isinstance(item, dict):
            raise ValueError("Each principal entry must be an object")

        principal_id = str(item.get("principal_id", "")).strip()
        role = str(item.get("role", "")).strip()

        # Support either pre-hashed 'token_hash' or plain 'token' for convenience
        token_hash_raw = item.get("token_hash")
        plain_token = item.get("token")
        if token_hash_raw and isinstance(token_hash_raw, str):
            token_hash = token_hash_raw.strip()
        elif plain_token and isinstance(plain_token, str):
            token_hash = hash_token(plain_token)
        else:
            raise ValueError(f"Principal '{principal_id}' must provide either 'token_hash' or 'token'")

        allowed_namespaces_raw = item.get("allowed_namespaces", ["*"])
        if isinstance(allowed_namespaces_raw, list):
            allowed_namespaces = tuple(str(ns).strip() for ns in allowed_namespaces_raw if str(ns).strip())
        else:
            allowed_namespaces = ("*",)

        rate_limit_rpm = int(item.get("rate_limit_rpm", 0))

        principal = Principal(
            principal_id=principal_id,
            token_hash=token_hash,
            role=role,
            allowed_namespaces=allowed_namespaces,
            rate_limit_rpm=rate_limit_rpm,
        )
        principals.append(principal)

    config = GatewayConfig(
        principals=tuple(principals),
        auth_mode=str(raw.get("auth_mode", "rbac")).strip(),
        audit_stream=str(raw.get("audit_stream", "stdout")).strip(),
        require_auth=bool(raw.get("require_auth", True)),
    )
    config.validate()
    return config


class RateLimiter:
    """Thread-safe in-memory sliding window rate limiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, list[float]] = {}

    def is_allowed(self, principal_id: str, limit_rpm: int) -> bool:
        if limit_rpm <= 0:
            return True
        now = time.time()
        cutoff = now - 60.0
        with self._lock:
            timestamps = self._buckets.setdefault(principal_id, [])
            while timestamps and timestamps[0] < cutoff:
                timestamps.pop(0)
            if len(timestamps) >= limit_rpm:
                return False
            timestamps.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class MultiTokenVerifier:
    """SDK TokenVerifier implementation matching incoming Bearer tokens against pre-provisioned principals."""

    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.rate_limiter = RateLimiter()

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        candidate_hash = hash_token(token)

        matched: Principal | None = None
        for principal in self.config.principals:
            if secrets.compare_digest(candidate_hash, principal.token_hash):
                matched = principal
                break

        if matched is None:
            return None

        return AccessToken(
            token="",
            client_id=matched.principal_id,
            scopes=[matched.role],
            claims={
                "principal_id": matched.principal_id,
                "role": matched.role,
                "allowed_namespaces": list(matched.allowed_namespaces),
                "rate_limit_rpm": matched.rate_limit_rpm,
            },
        )
