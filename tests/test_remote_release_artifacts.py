from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_workflows_parse_and_require_http_deployment_checks() -> None:
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        assert isinstance(yaml.safe_load(path.read_text()), dict)
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    compat = (ROOT / ".github/workflows/mcp-compat.yml").read_text()
    assert "scripts/check_container_deployment.py" in ci
    container_step = ci.split("Prove container replacement", 1)[1].split("- name:", 1)[0]
    assert "run: |" in container_step
    assert 'version="$(' in container_step
    assert "--expected-version" in container_step.split('version="$(', 1)[1]
    assert "scripts/check_http_deployment.py" in ci
    assert compat.count("scripts/check_http_deployment.py") == 2
    assert "python-mcp-compat," in ci


def test_release_publication_binds_immutable_source() -> None:
    for name in ("release.yml", "container-release.yml"):
        text = (ROOT / ".github/workflows" / name).read_text()
        assert "ref: ${{ github.sha }}" in text
        assert "refs/tags/$RELEASE_TAG^{commit}" in text
        assert "main" not in text
    container = (ROOT / ".github/workflows/container-release.yml").read_text()
    assert "environment: ghcr" in container
    assert container.index("scripts/check_container_deployment.py") < container.index("docker push")


def test_compose_private_defaults_and_persistence() -> None:
    compose = yaml.safe_load((ROOT / "compose.yml").read_text())
    server = compose["services"]["amb-mcp"]
    assert server["user"] == "10001:10001"
    assert server["read_only"] is True
    assert all(port.startswith("127.0.0.1:") for port in server["ports"])
    assert "amb-home:/data/agent-memory-bridge" in server["volumes"]
    assert server["secrets"] == ["amb_http_token"]


def test_outage_probe_does_not_accept_auth_or_programming_errors() -> None:
    spec = importlib.util.spec_from_file_location("remote_first_win", ROOT / "scripts/check_remote_first_win.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.connection_unavailable(httpx2.ConnectError("fixture unavailable"))
    assert module.connection_unavailable(httpx2.RemoteProtocolError("fixture tunnel upstream closed"))
    assert module.connection_unavailable(httpx2.ReadError("fixture tunnel reset"))
    assert module.connection_unavailable(ExceptionGroup("fixture", [httpx2.ConnectError("unavailable")]))
    assert not module.connection_unavailable(ValueError("fixture programming error"))
    assert not module.connection_unavailable(RuntimeError("fixture unauthorized"))


def test_http_acceptance_isolates_environment_and_rejects_wrong_content(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("http_check", ROOT / "scripts/check_http_deployment.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND", "must-not-run")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RECALL_RECEIPT_SECRET_PATH", "/must-not-write")
    env = module._runtime_env(tmp_path)
    assert "AGENT_MEMORY_BRIDGE_EMBEDDING_COMMAND" not in env
    assert "AGENT_MEMORY_BRIDGE_RECALL_RECEIPT_SECRET_PATH" not in env
    assert env["AGENT_MEMORY_BRIDGE_CONFIG"] == str(tmp_path / "config.toml")
    assert not module._exact_recall({"count": 1, "items": [{"content": "different"}]}, "expected")
    assert module._exact_recall({"count": 1, "items": [{"content": "expected"}]}, "expected")
    first = SimpleNamespace(name="store", input_schema={"type": "object"}, output_schema=None)
    other = SimpleNamespace(name="store", input_schema={"type": "string"}, output_schema=None)
    assert module._contract_digest([first]) != module._contract_digest([other])


def test_export_acceptance_rejects_corruption_and_legacy_error() -> None:
    spec = importlib.util.spec_from_file_location("export_check", ROOT / "scripts/run_http_acceptance.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    observed = {"count": 500, "content_sha256": "0" * 64}
    assert not module._evaluate_export(observed, expected_count=500, expected_digest="1" * 64)["exact"]
    with pytest.raises(RuntimeError, match="returned an error"):
        module._result_payload(SimpleNamespace(isError=True, structuredContent={"count": 500, "content": "bad"}))
