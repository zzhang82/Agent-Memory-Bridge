from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from agent_mem_bridge.mcp_boundary import (
    DISCOVER_CACHE_HINT,
    MCP_LEGACY_TEST_VERSION,
    MCP_MODERN_VERSION,
    PUBLIC_TOOL_ORDER,
    TOOLS_LIST_CACHE_HINT,
)
from agent_mem_bridge.schema import CURRENT_SCHEMA_VERSION, schema_version
from agent_mem_bridge.storage import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "mcp_wire" / "requests.json"
PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"


class RawStdioSession:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.db_path = runtime_dir / "bridge.db"
        self.process: asyncio.subprocess.Process | None = None

    async def __aenter__(self) -> RawStdioSession:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "AGENT_MEMORY_BRIDGE_HOME": str(self.runtime_dir),
            "AGENT_MEMORY_BRIDGE_DB_PATH": str(self.db_path),
            "AGENT_MEMORY_BRIDGE_LOG_DIR": str(self.runtime_dir / "logs"),
        }
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "agent_mem_bridge",
            cwd=ROOT,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        assert self.process is not None
        if self.process.stdin is not None:
            self.process.stdin.close()
            await self.process.stdin.wait_closed()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except TimeoutError:
            self.process.terminate()
            await self.process.wait()

    async def notify(self, request: dict[str, Any]) -> None:
        await self._write(request)

    async def request(self, request: dict[str, Any]) -> dict[str, Any]:
        await self._write(request)
        assert self.process is not None and self.process.stdout is not None
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout=10)
        if not line:
            stderr = await self._stderr_text()
            raise AssertionError(f"MCP server closed before responding; stderr={stderr!r}")
        response = json.loads(line)
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == request["id"]
        return response

    async def _write(self, request: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
        await self.process.stdin.drain()

    async def _stderr_text(self) -> str:
        assert self.process is not None and self.process.stderr is not None
        try:
            raw = await asyncio.wait_for(self.process.stderr.read(), timeout=1)
        except TimeoutError:
            return ""
        return raw.decode("utf-8", errors="replace")


def _fixtures() -> dict[str, dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _database_dump(db_path: Path) -> tuple[str, ...]:
    with sqlite3.connect(db_path) as conn:
        return tuple(sorted(conn.iterdump()))


def _modern_meta(*, version: str = MCP_MODERN_VERSION) -> dict[str, Any]:
    return {
        PROTOCOL_VERSION_KEY: version,
        CLIENT_INFO_KEY: {"name": "amb-raw-wire-proof", "version": "0.26.1"},
        CLIENT_CAPABILITIES_KEY: {},
    }


def _tool_call(
    fixtures: dict[str, dict[str, Any]],
    fixture_name: str,
    *,
    request_id: int,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    request = copy.deepcopy(fixtures[fixture_name])
    request["id"] = request_id
    request["params"]["name"] = name
    request["params"]["arguments"] = arguments
    return request


async def _exercise_raw_eras(tmp_path: Path) -> None:
    fixtures = _fixtures()
    modern_dir = tmp_path / "modern"
    async with RawStdioSession(modern_dir) as session:
        discover = await session.request(copy.deepcopy(fixtures["modern_discover"]))
        discover_result = discover["result"]
        assert discover_result["supportedVersions"] == [MCP_MODERN_VERSION]
        assert discover_result["resultType"] == "complete"
        assert discover_result["ttlMs"] == DISCOVER_CACHE_HINT.ttl_ms
        assert discover_result["cacheScope"] == DISCOVER_CACHE_HINT.scope
        assert discover_result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "agent-memory-bridge"

        tools = await session.request(copy.deepcopy(fixtures["modern_tools_list"]))
        tools_result = tools["result"]
        assert tools_result["resultType"] == "complete"
        assert tools_result["ttlMs"] == TOOLS_LIST_CACHE_HINT.ttl_ms
        assert tools_result["cacheScope"] == TOOLS_LIST_CACHE_HINT.scope
        assert [tool["name"] for tool in tools_result["tools"]] == list(PUBLIC_TOOL_ORDER)

        stats = await session.request(copy.deepcopy(fixtures["modern_stats_call"]))
        assert stats["result"]["resultType"] == "complete"
        assert stats["result"]["structuredContent"]["total_count"] == 0

        begun = await session.request(
            _tool_call(
                fixtures,
                "modern_stats_call",
                request_id=4,
                name="begin_run",
                arguments={
                    "workspace_key": "project:raw-wire-episode-modern",
                    "goal": "Prove the modern raw-wire episode flow.",
                    "idempotency_key": "begin:raw-wire-modern",
                },
            )
        )
        begun_payload = begun["result"]["structuredContent"]
        assert begun["result"]["resultType"] == "complete"
        assert begun_payload["run_id"].startswith("run_")
        private_event = await session.request(
            _tool_call(
                fixtures,
                "modern_stats_call",
                request_id=41,
                name="record_run_event",
                arguments={
                    "workspace_key": "project:raw-wire-episode-modern",
                    "run_id": begun_payload["run_id"],
                    "work_item_id": begun_payload["root_work_item_id"],
                    "event_type": "checkpoint",
                    "summary": "Reject hidden reasoning at the MCP boundary.",
                    "payload": {"nested": {"Thought-Process": "private"}},
                    "idempotency_key": "event:raw-wire-modern:private",
                },
            )
        )
        _assert_raw_tool_error(private_event)
        recorded = await session.request(
            _tool_call(
                fixtures,
                "modern_stats_call",
                request_id=5,
                name="record_run_event",
                arguments={
                    "workspace_key": "project:raw-wire-episode-modern",
                    "run_id": begun_payload["run_id"],
                    "work_item_id": begun_payload["root_work_item_id"],
                    "event_type": "checkpoint",
                    "summary": "Modern raw-wire episode checkpoint passed.",
                    "idempotency_key": "event:raw-wire-modern",
                },
            )
        )
        assert recorded["result"]["structuredContent"]["sequence"] == 1
        terminal = await session.request(
            _tool_call(
                fixtures,
                "modern_stats_call",
                request_id=51,
                name="record_run_event",
                arguments={
                    "workspace_key": "project:raw-wire-episode-modern",
                    "run_id": begun_payload["run_id"],
                    "work_item_id": begun_payload["root_work_item_id"],
                    "event_type": "work_item_completed",
                    "summary": "Modern raw-wire root work item completed.",
                    "idempotency_key": "event:raw-wire-modern:completed",
                },
            )
        )
        assert terminal["result"]["structuredContent"]["sequence"] == 2
        restored = await session.request(
            _tool_call(
                fixtures,
                "modern_stats_call",
                request_id=6,
                name="get_run",
                arguments={
                    "workspace_key": "project:raw-wire-episode-modern",
                    "run_id": begun_payload["run_id"],
                },
            )
        )
        assert restored["result"]["structuredContent"]["latest_sequence"] == 2
        completed = await session.request(
            _tool_call(
                fixtures,
                "modern_stats_call",
                request_id=7,
                name="complete_run",
                arguments={
                    "workspace_key": "project:raw-wire-episode-modern",
                    "run_id": begun_payload["run_id"],
                    "outcome": "unverified",
                    "evaluator_type": "agent",
                    "evidence": [{"kind": "raw-wire-proof", "era": "modern", "passed": True}],
                    "idempotency_key": "outcome:raw-wire-modern",
                },
            )
        )
        assert completed["result"]["structuredContent"]["outcome"] == "unverified"

        raw_secret = "raw-baggage-secret-must-not-persist"
        capability_secret = "raw-capability-secret-must-not-persist"
        store_request = copy.deepcopy(fixtures["modern_stats_call"])
        store_request["id"] = 8
        store_request["params"]["name"] = "store"
        store_request["params"]["arguments"] = {
            "namespace": "project:raw-wire-proof",
            "content": "Raw wire metadata persistence boundary proof.",
            "kind": "memory",
        }
        store_request["params"]["_meta"].update(
            {
                "baggage": raw_secret,
                "request-id": "private-request-id",
                CLIENT_CAPABILITIES_KEY: {"experimental": {"raw-proof": {"secret": capability_secret}}},
            }
        )
        stored = await session.request(store_request)
        assert "result" in stored, stored
        assert stored["result"]["resultType"] == "complete"
        assert stored["result"]["structuredContent"]["stored"] is True

    encoded_dump = "\n".join(_database_dump(modern_dir / "bridge.db"))
    assert raw_secret not in encoded_dump
    assert capability_secret not in encoded_dump
    assert "private-request-id" not in encoded_dump

    legacy_dir = tmp_path / "legacy"
    async with RawStdioSession(legacy_dir) as session:
        initialized = await session.request(copy.deepcopy(fixtures["legacy_initialize"]))
        initialize_result = initialized["result"]
        assert initialize_result["protocolVersion"] == MCP_LEGACY_TEST_VERSION
        assert "resultType" not in initialize_result
        assert "ttlMs" not in initialize_result
        assert "cacheScope" not in initialize_result
        await session.notify(copy.deepcopy(fixtures["legacy_initialized"]))

        tools = await session.request(copy.deepcopy(fixtures["legacy_tools_list"]))
        tools_result = tools["result"]
        assert [tool["name"] for tool in tools_result["tools"]] == list(PUBLIC_TOOL_ORDER)
        assert "resultType" not in tools_result
        assert "ttlMs" not in tools_result
        assert "cacheScope" not in tools_result

        stats = await session.request(copy.deepcopy(fixtures["legacy_stats_call"]))
        assert stats["result"]["structuredContent"]["total_count"] == 0
        assert "resultType" not in stats["result"]

        begun = await session.request(
            _tool_call(
                fixtures,
                "legacy_stats_call",
                request_id=4,
                name="begin_run",
                arguments={
                    "workspace_key": "project:raw-wire-episode-legacy",
                    "goal": "Prove the legacy raw-wire episode flow.",
                    "idempotency_key": "begin:raw-wire-legacy",
                },
            )
        )
        begun_payload = begun["result"]["structuredContent"]
        assert begun_payload["run_id"].startswith("run_")
        assert "resultType" not in begun["result"]
        private_event = await session.request(
            _tool_call(
                fixtures,
                "legacy_stats_call",
                request_id=41,
                name="record_run_event",
                arguments={
                    "workspace_key": "project:raw-wire-episode-legacy",
                    "run_id": begun_payload["run_id"],
                    "work_item_id": begun_payload["root_work_item_id"],
                    "event_type": "checkpoint",
                    "summary": "Reject hidden reasoning at the MCP boundary.",
                    "payload": {"nested": {"Thought-Process": "private"}},
                    "idempotency_key": "event:raw-wire-legacy:private",
                },
            )
        )
        _assert_raw_tool_error(private_event)
        recorded = await session.request(
            _tool_call(
                fixtures,
                "legacy_stats_call",
                request_id=5,
                name="record_run_event",
                arguments={
                    "workspace_key": "project:raw-wire-episode-legacy",
                    "run_id": begun_payload["run_id"],
                    "work_item_id": begun_payload["root_work_item_id"],
                    "event_type": "checkpoint",
                    "summary": "Legacy raw-wire episode checkpoint passed.",
                    "idempotency_key": "event:raw-wire-legacy",
                },
            )
        )
        assert recorded["result"]["structuredContent"]["sequence"] == 1
        terminal = await session.request(
            _tool_call(
                fixtures,
                "legacy_stats_call",
                request_id=51,
                name="record_run_event",
                arguments={
                    "workspace_key": "project:raw-wire-episode-legacy",
                    "run_id": begun_payload["run_id"],
                    "work_item_id": begun_payload["root_work_item_id"],
                    "event_type": "work_item_completed",
                    "summary": "Legacy raw-wire root work item completed.",
                    "idempotency_key": "event:raw-wire-legacy:completed",
                },
            )
        )
        assert terminal["result"]["structuredContent"]["sequence"] == 2
        restored = await session.request(
            _tool_call(
                fixtures,
                "legacy_stats_call",
                request_id=6,
                name="get_run",
                arguments={
                    "workspace_key": "project:raw-wire-episode-legacy",
                    "run_id": begun_payload["run_id"],
                },
            )
        )
        assert restored["result"]["structuredContent"]["latest_sequence"] == 2
        completed = await session.request(
            _tool_call(
                fixtures,
                "legacy_stats_call",
                request_id=7,
                name="complete_run",
                arguments={
                    "workspace_key": "project:raw-wire-episode-legacy",
                    "run_id": begun_payload["run_id"],
                    "outcome": "unverified",
                    "evaluator_type": "agent",
                    "evidence": [{"kind": "raw-wire-proof", "era": "legacy", "passed": True}],
                    "idempotency_key": "outcome:raw-wire-legacy",
                },
            )
        )
        assert completed["result"]["structuredContent"]["outcome"] == "unverified"
        assert "resultType" not in completed["result"]


def test_raw_wire_modern_and_legacy_contracts(tmp_path: Path) -> None:
    asyncio.run(_exercise_raw_eras(tmp_path))


async def _exercise_raw_attribution_era(
    session: RawStdioSession,
    fixtures: dict[str, dict[str, Any]],
    *,
    era: str,
    fixture_name: str,
) -> None:
    workspace_key = f"project:raw-wire-attribution-{era}"
    if era == "modern":
        discovered = await session.request(copy.deepcopy(fixtures["modern_discover"]))
        assert discovered["result"]["supportedVersions"] == [MCP_MODERN_VERSION]
    else:
        initialized = await session.request(copy.deepcopy(fixtures["legacy_initialize"]))
        assert initialized["result"]["protocolVersion"] == MCP_LEGACY_TEST_VERSION
        await session.notify(copy.deepcopy(fixtures["legacy_initialized"]))

    stored = await session.request(
        _tool_call(
            fixtures,
            fixture_name,
            request_id=2,
            name="store",
            arguments={
                "namespace": workspace_key,
                "content": f"Raw wire {era} receipt attribution contract.",
                "kind": "memory",
            },
        )
    )
    stored_payload = stored["result"]["structuredContent"]
    recalled = await session.request(
        _tool_call(
            fixtures,
            fixture_name,
            request_id=3,
            name="recall",
            arguments={
                "namespace": workspace_key,
                "query": f"raw wire {era} receipt attribution",
                "kind": "memory",
                "limit": 5,
            },
        )
    )
    recall_payload = recalled["result"]["structuredContent"]
    memory_item = next(item for item in recall_payload["items"] if item["id"] == stored_payload["id"])
    memory_id = str(stored_payload["id"])
    result_rank = recall_payload["items"].index(memory_item) + 1
    receipt_token = str(recall_payload["recall_receipt"]["token"])
    receipt_hash = hashlib.sha256(receipt_token.encode("utf-8")).hexdigest()

    begun = await session.request(
        _tool_call(
            fixtures,
            fixture_name,
            request_id=4,
            name="begin_run",
            arguments={
                "workspace_key": workspace_key,
                "goal": f"Prove {era} raw-wire receipt attribution.",
                "idempotency_key": f"begin:raw-wire-attribution:{era}",
            },
        )
    )
    begun_payload = begun["result"]["structuredContent"]
    run_id = str(begun_payload["run_id"])
    work_item_id = str(begun_payload["root_work_item_id"])
    attribution = {
        "namespace": workspace_key,
        "recall_receipt": receipt_token,
        "items": [{"memory_id": memory_id, "result_rank": result_rank}],
    }
    expected_payload = {
        "result_ids": [memory_id],
        "result_ranks": [result_rank],
        "receipt_hash": receipt_hash,
    }

    invalid = await session.request(
        _tool_call(
            fixtures,
            fixture_name,
            request_id=5,
            name="record_run_event",
            arguments={
                "workspace_key": workspace_key,
                "run_id": run_id,
                "work_item_id": work_item_id,
                "event_type": "memory_recalled",
                "summary": "This raw-wire receipt hash is intentionally mismatched.",
                "idempotency_key": f"event:raw-wire-attribution:{era}:invalid",
                "payload": {**expected_payload, "receipt_hash": "0" * 64},
                "memory_attribution": attribution,
            },
        )
    )
    _assert_raw_tool_error(invalid)
    assert "receipt_hash" in json.dumps(invalid, sort_keys=True)
    after_invalid = await session.request(
        _tool_call(
            fixtures,
            fixture_name,
            request_id=6,
            name="get_run",
            arguments={"workspace_key": workspace_key, "run_id": run_id},
        )
    )
    assert after_invalid["result"]["structuredContent"]["latest_sequence"] == 0
    assert after_invalid["result"]["structuredContent"]["events"] == []

    memory_recalled = await session.request(
        _tool_call(
            fixtures,
            fixture_name,
            request_id=7,
            name="record_run_event",
            arguments={
                "workspace_key": workspace_key,
                "run_id": run_id,
                "work_item_id": work_item_id,
                "event_type": "memory_recalled",
                "summary": "The agent received one receipt-bound durable memory exposure.",
                "idempotency_key": f"event:raw-wire-attribution:{era}:recalled",
                "payload": expected_payload,
                "memory_attribution": attribution,
            },
        )
    )
    memory_recalled_payload = memory_recalled["result"]["structuredContent"]
    assert memory_recalled_payload["sequence"] == 1
    assert receipt_token not in json.dumps(memory_recalled_payload, sort_keys=True)

    applied = await session.request(
        _tool_call(
            fixtures,
            fixture_name,
            request_id=8,
            name="record_run_event",
            arguments={
                "workspace_key": workspace_key,
                "run_id": run_id,
                "work_item_id": work_item_id,
                "event_type": "memory_applied",
                "summary": "The agent explicitly applied the recalled memory.",
                "idempotency_key": f"event:raw-wire-attribution:{era}:applied",
                "payload": {
                    **expected_payload,
                    "source_recall_event_id": memory_recalled_payload["event_id"],
                },
                "memory_attribution": {
                    "source_recall_event_id": memory_recalled_payload["event_id"],
                    "items": [{"memory_id": memory_id, "result_rank": result_rank}],
                },
            },
        )
    )
    assert applied["result"]["structuredContent"]["sequence"] == 2
    terminal = await session.request(
        _tool_call(
            fixtures,
            fixture_name,
            request_id=81,
            name="record_run_event",
            arguments={
                "workspace_key": workspace_key,
                "run_id": run_id,
                "work_item_id": work_item_id,
                "event_type": "work_item_completed",
                "summary": "The raw-wire attribution root work item completed.",
                "idempotency_key": f"event:raw-wire-attribution:{era}:completed",
            },
        )
    )
    assert terminal["result"]["structuredContent"]["sequence"] == 3

    restored = await session.request(
        _tool_call(
            fixtures,
            fixture_name,
            request_id=9,
            name="get_run",
            arguments={"workspace_key": workspace_key, "run_id": run_id},
        )
    )
    restored_payload = restored["result"]["structuredContent"]
    assert restored_payload["latest_sequence"] == 3
    assert [event["event_type"] for event in restored_payload["events"]] == [
        "memory_recalled",
        "memory_applied",
        "work_item_completed",
    ]
    assert receipt_token not in json.dumps(restored_payload, sort_keys=True)
    assert "recall_receipt" not in json.dumps(restored_payload, sort_keys=True)

    completed = await session.request(
        _tool_call(
            fixtures,
            fixture_name,
            request_id=10,
            name="complete_run",
            arguments={
                "workspace_key": workspace_key,
                "run_id": run_id,
                "outcome": "unverified",
                "evaluator_type": "agent",
                "evidence": [{"kind": "raw-wire-attribution", "era": era, "passed": True}],
                "idempotency_key": f"outcome:raw-wire-attribution:{era}",
            },
        )
    )
    assert completed["result"]["structuredContent"]["outcome"] == "unverified"
    if era == "modern":
        assert completed["result"]["resultType"] == "complete"
    else:
        assert "resultType" not in completed["result"]


def _assert_raw_tool_error(response: dict[str, Any]) -> None:
    if "error" in response:
        assert isinstance(response["error"].get("message"), str)
        return
    assert response["result"]["isError"] is True


async def _exercise_raw_attribution_eras(tmp_path: Path) -> None:
    fixtures = _fixtures()
    async with RawStdioSession(tmp_path / "modern") as session:
        await _exercise_raw_attribution_era(session, fixtures, era="modern", fixture_name="modern_stats_call")
    async with RawStdioSession(tmp_path / "legacy") as session:
        await _exercise_raw_attribution_era(session, fixtures, era="legacy", fixture_name="legacy_stats_call")


def test_raw_wire_modern_and_legacy_memory_attribution_contracts(tmp_path: Path) -> None:
    asyncio.run(_exercise_raw_attribution_eras(tmp_path))


async def _exercise_failed_negotiation(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge.db"
    MemoryStore(db_path=db_path, log_dir=tmp_path / "logs")
    before = _database_dump(db_path)

    unsupported = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "server/discover",
        "params": {"_meta": _modern_meta(version="2099-01-01")},
    }
    async with RawStdioSession(tmp_path) as session:
        response = await session.request(unsupported)
        assert response["error"]["code"] == -32022
        assert response["error"]["data"] == {
            "supported": [MCP_MODERN_VERSION],
            "requested": "2099-01-01",
        }

        valid = await session.request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "server/discover",
                "params": {"_meta": _modern_meta()},
            }
        )
        assert valid["result"]["supportedVersions"] == [MCP_MODERN_VERSION]

        missing_envelope = await session.request({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        assert missing_envelope["error"]["code"] == -32602
        assert "required" in missing_envelope["error"]["message"]
        assert "envelope keys" in missing_envelope["error"]["message"]

    assert _database_dump(db_path) == before

    malformed_dir = tmp_path / "malformed-client-info"
    malformed_db = malformed_dir / "bridge.db"
    MemoryStore(db_path=malformed_db, log_dir=malformed_dir / "logs")
    malformed_before = _database_dump(malformed_db)
    malformed = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "server/discover",
        "params": {
            "_meta": {
                **_modern_meta(),
                CLIENT_INFO_KEY: {"name": ["invalid"], "version": {"invalid": True}},
            }
        },
    }
    async with RawStdioSession(malformed_dir) as session:
        response = await session.request(malformed)
        assert response["error"]["code"] == -32602
    assert _database_dump(malformed_db) == malformed_before


def test_raw_wire_negotiation_errors_are_explicit_and_read_only(tmp_path: Path) -> None:
    asyncio.run(_exercise_failed_negotiation(tmp_path))
    with sqlite3.connect(tmp_path / "bridge.db") as conn:
        assert schema_version(conn) == CURRENT_SCHEMA_VERSION == 11
