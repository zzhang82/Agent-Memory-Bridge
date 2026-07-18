from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_mem_bridge.cross_client_activation import (
    ACTIVATION_RECEIPT_SCHEMA,
    READER_TAG,
    REVIEWED_TAG,
    WORKFLOW_TAG,
    WRITER_TAG,
    build_activation_receipt,
    render_activation_receipt_markdown,
)
from agent_mem_bridge.release_contract import load_server_tool_names
from agent_mem_bridge.storage import MemoryStore


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "ack_signal",
    "browse",
    "claim_signal",
    "extend_signal_lease",
    "export",
    "forget",
    "promote",
    "recall",
    "stats",
    "store",
}


def test_activation_receipt_passes_for_acked_cross_client_observation(tmp_path: Path) -> None:
    store, namespace, correlation_id, writer_id, reader_id = _seed_activation(tmp_path)

    receipt = build_activation_receipt(store, namespace=namespace, correlation_id=correlation_id)

    assert receipt["schema"] == ACTIVATION_RECEIPT_SCHEMA
    assert receipt["status"] == "pass"
    assert receipt["reason_codes"] == []
    assert receipt["declared_provenance_only"] is True
    assert receipt["authenticated_origin"] is False
    assert receipt["external_adoption_claim"] is False
    assert receipt["public_mcp_surface_change"] is False
    assert receipt["durable_writeback_count"] == 0
    assert receipt["config_write_count"] == 0
    assert receipt["source_client_relation"] == "distinct_declared_values"
    assert receipt["writer"]["matched_count"] == 1
    assert receipt["writer"]["reviewed"] is True
    assert receipt["reader"]["matched_count"] == 1
    assert receipt["reader"]["signal_status"] == "acked"
    assert receipt["reader"]["observed_memory_matches_writer"] is True
    encoded = json.dumps(receipt, sort_keys=True)
    assert namespace not in encoded
    assert correlation_id not in encoded
    assert writer_id not in encoded
    assert reader_id not in encoded


def test_activation_receipt_requires_distinct_declared_source_clients(tmp_path: Path) -> None:
    store, namespace, correlation_id, _, _ = _seed_activation(
        tmp_path,
        writer_source_client="codex",
        reader_source_client="codex",
    )

    receipt = build_activation_receipt(store, namespace=namespace, correlation_id=correlation_id)

    assert receipt["status"] == "review_required"
    assert receipt["reason_codes"] == ["source_client_not_cross_client"]
    assert receipt["source_client_relation"] == "same_declared_value"


def test_activation_receipt_source_client_identity_is_trimmed_and_casefolded(tmp_path: Path) -> None:
    store, namespace, correlation_id, _, _ = _seed_activation(
        tmp_path,
        writer_source_client=" Codex ",
        reader_source_client="codex",
    )

    receipt = build_activation_receipt(store, namespace=namespace, correlation_id=correlation_id)

    assert receipt["status"] == "review_required"
    assert receipt["reason_codes"] == ["source_client_not_cross_client"]
    assert receipt["source_client_relation"] == "same_declared_value"
    encoded = json.dumps(receipt, sort_keys=True)
    assert "Codex" not in encoded
    assert "codex" not in encoded


def test_activation_receipt_requires_reviewed_writer_memory(tmp_path: Path) -> None:
    store, namespace, correlation_id, _, _ = _seed_activation(tmp_path, writer_reviewed=False)

    receipt = build_activation_receipt(store, namespace=namespace, correlation_id=correlation_id)

    assert receipt["status"] == "review_required"
    assert receipt["reason_codes"] == ["writer_memory_not_reviewed"]
    assert receipt["writer"]["matched_count"] == 1
    assert receipt["writer"]["reviewed"] is False


def test_activation_receipt_requires_reader_ack(tmp_path: Path) -> None:
    store, namespace, correlation_id, _, _ = _seed_activation(tmp_path, ack_reader=False)

    receipt = build_activation_receipt(store, namespace=namespace, correlation_id=correlation_id)

    assert receipt["status"] == "review_required"
    assert receipt["reason_codes"] == ["reader_signal_not_acked"]
    assert receipt["reader"]["signal_status"] == "pending"


def test_activation_receipt_requires_observed_id_to_match_writer(tmp_path: Path) -> None:
    store, namespace, correlation_id, _, _ = _seed_activation(tmp_path, observed_memory_id="not-the-writer")

    receipt = build_activation_receipt(store, namespace=namespace, correlation_id=correlation_id)

    assert receipt["status"] == "review_required"
    assert receipt["reason_codes"] == ["reader_observed_memory_id_mismatch"]
    assert receipt["reader"]["observed_memory_matches_writer"] is False


def test_activation_receipt_requires_declared_source_client_provenance(tmp_path: Path) -> None:
    store, namespace, correlation_id, _, _ = _seed_activation(tmp_path, reader_source_client=None)

    receipt = build_activation_receipt(store, namespace=namespace, correlation_id=correlation_id)

    assert receipt["status"] == "review_required"
    assert receipt["reason_codes"] == ["reader_source_client_missing"]
    assert receipt["reader"]["source_client_present"] is False
    assert receipt["source_client_relation"] == "incomplete_declared_values"


def test_activation_receipt_rejects_unexpected_correlation_records(tmp_path: Path) -> None:
    store, namespace, correlation_id, _, _ = _seed_activation(tmp_path)
    store.store(
        namespace=namespace,
        kind="memory",
        title="unrelated correlation row",
        content="unexpected correlation evidence",
        tags=["kind:learn"],
        correlation_id=correlation_id,
        source_client="third-client",
    )

    receipt = build_activation_receipt(store, namespace=namespace, correlation_id=correlation_id)

    assert receipt["status"] == "review_required"
    assert receipt["reason_codes"] == ["unexpected_correlation_records"]
    assert receipt["matching_record_count"] == 3


def test_activation_receipt_output_is_deterministic_and_redacted(tmp_path: Path) -> None:
    store, namespace, correlation_id, writer_id, reader_id = _seed_activation(
        tmp_path,
        writer_content="activation writer\npath: D:\\secret\\project\nsession: session-secret",
        reader_extra={"path": "D:\\secret\\reader", "client_session_id": "reader-session-secret"},
        writer_session_id="writer-session-secret",
        reader_session_id="reader-session-secret",
        writer_client_session_id="writer-client-session-secret",
        reader_client_session_id="reader-client-session-secret",
        writer_source_model="model-secret",
        reader_source_model="model-secret",
    )

    first = build_activation_receipt(store, namespace=namespace, correlation_id=correlation_id)
    second = build_activation_receipt(store, namespace=namespace, correlation_id=correlation_id)
    markdown = render_activation_receipt_markdown(first)

    assert first == second
    encoded = json.dumps(first, sort_keys=True) + markdown
    forbidden = [
        namespace,
        correlation_id,
        writer_id,
        reader_id,
        "D:\\secret",
        "session-secret",
        "writer-session-secret",
        "reader-session-secret",
        "writer-client-session-secret",
        "reader-client-session-secret",
        "model-secret",
    ]
    for value in forbidden:
        assert value not in encoded
    assert "Declared provenance only" in markdown
    assert "does not authenticate client identity" in markdown
    assert "not proof of external adoption or vendor certification" in markdown


def test_activation_receipt_does_not_mutate_memory_rows(tmp_path: Path) -> None:
    store, namespace, correlation_id, _, _ = _seed_activation(tmp_path)
    before = _memory_rows(store)

    receipt = build_activation_receipt(store, namespace=namespace, correlation_id=correlation_id)

    assert receipt["status"] == "pass"
    assert _memory_rows(store) == before


def test_activation_receipt_cli_json_exit_code_pass(tmp_path: Path) -> None:
    store, namespace, correlation_id, _, _ = _seed_activation(tmp_path)

    completed = _run_cli(
        store,
        [
            "activation-receipt",
            "--namespace",
            namespace,
            "--correlation-id",
            correlation_id,
            "--format",
            "json",
        ],
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema"] == ACTIVATION_RECEIPT_SCHEMA
    assert payload["status"] == "pass"
    assert namespace not in completed.stdout
    assert correlation_id not in completed.stdout


def test_activation_receipt_cli_markdown_exit_code_review_required(tmp_path: Path) -> None:
    store, namespace, correlation_id, _, _ = _seed_activation(tmp_path, ack_reader=False)

    completed = _run_cli(
        store,
        [
            "activation-receipt",
            "--namespace",
            namespace,
            "--correlation-id",
            correlation_id,
            "--format",
            "markdown",
        ],
    )

    assert completed.returncode == 1
    assert "Status: `review_required`" in completed.stdout
    assert "reader_signal_not_acked" in completed.stdout
    assert "Declared provenance only" in completed.stdout
    assert "does not authenticate client identity" in completed.stdout
    assert "not proof of external adoption or vendor certification" in completed.stdout
    assert namespace not in completed.stdout
    assert correlation_id not in completed.stdout


def test_activation_receipt_preserves_public_mcp_surface() -> None:
    assert load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py") == EXPECTED_TOOLS


def test_activation_receipt_cli_missing_store_creates_no_db_or_log_paths(tmp_path: Path) -> None:
    runtime_root = tmp_path / "missing-runtime"
    db_path = runtime_root / "database" / "bridge.db"
    log_dir = runtime_root / "logs"
    namespace = "project:missing-store-secret"
    correlation_id = "missing-store-correlation-secret"

    completed = _run_cli_with_paths(
        db_path,
        log_dir,
        [
            "activation-receipt",
            "--namespace",
            namespace,
            "--correlation-id",
            correlation_id,
            "--format",
            "json",
        ],
    )

    assert completed.returncode == 1, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "review_required"
    assert payload["reason_codes"] == ["store_unavailable"]
    assert payload["store_available"] is False
    assert namespace not in completed.stdout
    assert correlation_id not in completed.stdout
    assert str(db_path) not in completed.stdout
    assert str(log_dir) not in completed.stdout
    assert not runtime_root.exists()


def test_activation_receipt_real_stdio_cross_client_flow_is_read_only_and_redacted(tmp_path: Path) -> None:
    namespace = "project:stdio-activation-secret"
    correlation_id = "stdio-activation-correlation-secret"
    store = MemoryStore(tmp_path / "stdio-activation.db", log_dir=tmp_path / "stdio-logs")
    writer_id, reader_id = asyncio.run(
        _exercise_cross_client_stdio(store, namespace=namespace, correlation_id=correlation_id)
    )
    before = _memory_rows(store)

    completed = _run_cli(
        store,
        [
            "activation-receipt",
            "--namespace",
            namespace,
            "--correlation-id",
            correlation_id,
            "--format",
            "json",
        ],
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "pass"
    assert payload["writer"]["reviewed"] is True
    assert payload["reader"]["signal_status"] == "acked"
    assert payload["reader"]["observed_memory_matches_writer"] is True
    forbidden = [
        namespace,
        correlation_id,
        writer_id,
        reader_id,
        "D:\\secret\\stdio-writer",
        "D:\\secret\\stdio-reader",
        "stdio-writer-session-secret",
        "stdio-reader-session-secret",
        "stdio-writer-model-secret",
        "stdio-reader-model-secret",
    ]
    for value in forbidden:
        assert value not in completed.stdout
    assert '"client-a"' not in completed.stdout
    assert '"client-b"' not in completed.stdout
    assert _memory_rows(store) == before


def _seed_activation(
    tmp_path: Path,
    *,
    writer_source_client: str | None = "codex",
    reader_source_client: str | None = "opencode",
    writer_reviewed: bool = True,
    ack_reader: bool = True,
    observed_memory_id: str | None = None,
    writer_content: str = "record_type: learn\nclaim: cross-client activation writer",
    reader_extra: dict[str, Any] | None = None,
    writer_session_id: str | None = None,
    reader_session_id: str | None = None,
    writer_client_session_id: str | None = None,
    reader_client_session_id: str | None = None,
    writer_source_model: str | None = None,
    reader_source_model: str | None = None,
) -> tuple[MemoryStore, str, str, str, str]:
    namespace = "project:activation-test"
    correlation_id = f"activation-correlation-{tmp_path.name}"
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    writer_tags = [WORKFLOW_TAG, WRITER_TAG]
    if writer_reviewed:
        writer_tags.append(REVIEWED_TAG)
    writer = store.store(
        namespace=namespace,
        kind="memory",
        title="activation writer",
        content=writer_content,
        tags=writer_tags,
        correlation_id=correlation_id,
        source_client=writer_source_client,
        session_id=writer_session_id,
        client_session_id=writer_client_session_id,
        source_model=writer_source_model,
    )
    observed_id = writer["id"] if observed_memory_id is None else observed_memory_id
    reader_content = {"observed_memory_id": observed_id, **(reader_extra or {})}
    reader = store.store(
        namespace=namespace,
        kind="signal",
        title="activation reader",
        content=json.dumps(reader_content, sort_keys=True),
        tags=[WORKFLOW_TAG, READER_TAG],
        correlation_id=correlation_id,
        source_client=reader_source_client,
        session_id=reader_session_id,
        client_session_id=reader_client_session_id,
        source_model=reader_source_model,
    )
    if ack_reader:
        store.ack_signal(reader["id"])
    return store, namespace, correlation_id, str(writer["id"]), str(reader["id"])


async def _exercise_cross_client_stdio(
    store: MemoryStore,
    *,
    namespace: str,
    correlation_id: str,
) -> tuple[str, str]:
    async with stdio_client(_stdio_server_params(store, source_client="client-a")) as (read, write):
        async with ClientSession(read, write) as client_a:
            await client_a.initialize()
            await _assert_exact_tool_surface(client_a)
            writer = await client_a.call_tool(
                "store",
                arguments={
                    "namespace": namespace,
                    "kind": "memory",
                    "title": "stdio activation writer",
                    "content": "activation writer evidence\npath: D:\\secret\\stdio-writer",
                    "tags": [WORKFLOW_TAG, WRITER_TAG, REVIEWED_TAG],
                    "correlation_id": correlation_id,
                    "source_client": "client-a",
                    "session_id": "stdio-writer-session-secret",
                    "source_model": "stdio-writer-model-secret",
                },
            )
            writer_id = str(writer.structuredContent["id"])

    async with stdio_client(_stdio_server_params(store, source_client="client-b")) as (read, write):
        async with ClientSession(read, write) as client_b:
            await client_b.initialize()
            await _assert_exact_tool_surface(client_b)
            recalled = await client_b.call_tool(
                "recall",
                arguments={
                    "namespace": namespace,
                    "query": "activation writer evidence",
                    "kind": "memory",
                    "correlation_id": correlation_id,
                    "limit": 5,
                },
            )
            assert recalled.structuredContent["count"] == 1
            observed_memory_id = str(recalled.structuredContent["items"][0]["id"])
            assert observed_memory_id == writer_id

            reader = await client_b.call_tool(
                "store",
                arguments={
                    "namespace": namespace,
                    "kind": "signal",
                    "title": "stdio activation reader",
                    "content": json.dumps(
                        {
                            "observed_memory_id": observed_memory_id,
                            "path": "D:\\secret\\stdio-reader",
                        },
                        sort_keys=True,
                    ),
                    "tags": [WORKFLOW_TAG, READER_TAG],
                    "correlation_id": correlation_id,
                    "source_client": "client-b",
                    "session_id": "stdio-reader-session-secret",
                    "source_model": "stdio-reader-model-secret",
                },
            )
            reader_id = str(reader.structuredContent["id"])
            acked = await client_b.call_tool("ack_signal", arguments={"id": reader_id})
            assert acked.structuredContent["acked"] is True

    return writer_id, reader_id


def _stdio_server_params(store: MemoryStore, *, source_client: str) -> StdioServerParameters:
    env = os.environ.copy()
    env["AGENT_MEMORY_BRIDGE_DB_PATH"] = str(store.db_path)
    env["AGENT_MEMORY_BRIDGE_LOG_DIR"] = str(store.log_dir)
    env["AGENT_MEMORY_BRIDGE_DEFAULT_SOURCE_CLIENT"] = source_client
    env["AGENT_MEMORY_BRIDGE_DEFAULT_CLIENT_TRANSPORT"] = "stdio"
    env["PYTHONPATH"] = str(ROOT / "src")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_mem_bridge"],
        cwd=str(ROOT),
        env=env,
    )


async def _assert_exact_tool_surface(session: ClientSession) -> None:
    tools_response = await session.list_tools()
    assert {tool.name for tool in tools_response.tools} == EXPECTED_TOOLS


def _memory_rows(store: MemoryStore) -> list[dict[str, Any]]:
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, namespace, kind, title, content, tags_json, session_id, actor,
                   correlation_id, source_app, source_client, source_model,
                   client_session_id, client_workspace, client_transport,
                   signal_status, claimed_by, claimed_at, lease_expires_at,
                   expires_at, acknowledged_at, is_learning_candidate,
                   lineage_status, lineage_issues_json, content_hash, created_at
            FROM memories
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _run_cli(store: MemoryStore, args: list[str]) -> subprocess.CompletedProcess[str]:
    return _run_cli_with_paths(store.db_path, store.log_dir, args)


def _run_cli_with_paths(
    db_path: Path,
    log_dir: Path,
    args: list[str],
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENT_MEMORY_BRIDGE_DB_PATH"] = str(db_path)
    env["AGENT_MEMORY_BRIDGE_LOG_DIR"] = str(log_dir)
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "agent_mem_bridge", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
