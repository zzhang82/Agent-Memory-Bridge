from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_mem_bridge.memory_cli import run_compile_index
from agent_mem_bridge.memory_index import (
    MAX_INDEX_BYTES,
    MAX_INDEX_LINES,
    build_memory_index_metadata,
    compile_memory_index,
    write_memory_index,
)
from agent_mem_bridge.storage import MemoryStore


def test_compile_memory_index_basic_and_deterministic(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:index-test",
        title="SQLite single authority",
        content="record_type: decision\nclaim: SQLite WAL is durable authority.",
        tags=["domain:storage", "topic:database"],
    )
    store.store(
        namespace="project:index-test",
        title="No background daemons",
        content="record_type: constraint\nclaim: No autonomous background daemons in core.",
        tags=["domain:architecture"],
    )
    store.store(
        namespace="project:index-test",
        title="Windows WAL timing gotcha",
        content="record_type: gotcha\nclaim: Truncate WAL before byte assertions.",
        tags=["gotcha", "windows"],
    )

    index_1 = compile_memory_index(store, namespace="project:index-test")
    index_2 = compile_memory_index(store, namespace="project:index-test")

    # Byte deterministic
    assert index_1 == index_2
    assert "# Memory Index: `project:index-test`" in index_1
    assert "## Decisions" in index_1
    assert "## Constraints" in index_1
    assert "## Gotchas & Guidelines" in index_1
    assert "SQLite single authority" in index_1
    assert "[storage, database]" in index_1
    assert "No background daemons" in index_1
    assert "Windows WAL timing gotcha" in index_1

    # Metadata
    meta = build_memory_index_metadata(index_1, namespace="project:index-test")
    assert meta["schema"] == "memory.compiled_index.v1"
    assert meta["record_count"] == 3
    assert meta["size_capped"] is True
    assert meta["line_count"] <= MAX_INDEX_LINES
    assert meta["byte_count"] <= MAX_INDEX_BYTES


def test_compile_memory_index_enforces_strict_size_caps(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")

    # Insert 250 records (more than the 200 line cap)
    for i in range(250):
        store.store(
            namespace="project:large-index",
            title=f"Decision number {i:03d} with extra descriptive text",
            content=f"record_type: decision\nclaim: Decision rule {i} for architecture.",
            tags=["domain:scalability"],
        )

    index_text = compile_memory_index(store, namespace="project:large-index")
    lines = index_text.splitlines()
    bytes_count = len(index_text.encode("utf-8"))

    # Must be strictly under ceilings
    assert len(lines) <= MAX_INDEX_LINES
    assert bytes_count <= MAX_INDEX_BYTES
    assert "additional records omitted; use targeted recall" in index_text


def test_compile_memory_index_write_to_file(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")
    store.store(
        namespace="project:write-test",
        title="Sample rule",
        content="record_type: decision\nclaim: Follow the rule.",
    )
    content = compile_memory_index(store, namespace="project:write-test")
    out_file = tmp_path / "MEMORY-INDEX.md"
    written = write_memory_index(content, out_file)
    assert written == out_file.resolve()
    assert out_file.is_file()
    assert out_file.read_text(encoding="utf-8") == content


def test_compile_index_cli_runner_and_fail_open(tmp_path: Path, monkeypatch, capsys) -> None:
    # 1. Fail-open test: database does not exist
    missing_db = tmp_path / "non_existent.db"
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(missing_db))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path))

    args_fail_open = argparse.Namespace(
        namespace="project:fail-open",
        out=None,
        max_lines=200,
        max_bytes=25600,
        format="markdown",
        json=False,
        fail_open=True,
    )
    rc = run_compile_index(args_fail_open)
    assert rc == 0
    out = capsys.readouterr().out
    assert "failing open" in out

    # 2. Strict mode: with fail_open=False, exits 1
    args_strict = argparse.Namespace(
        namespace="project:fail-open",
        out=None,
        max_lines=200,
        max_bytes=25600,
        format="markdown",
        json=False,
        fail_open=False,
    )
    rc_strict = run_compile_index(args_strict)
    assert rc_strict == 1
    err = capsys.readouterr().err
    assert "database not found" in err

    # 3. Successful CLI run with store
    db_path = tmp_path / "bridge.db"
    store = MemoryStore(db_path, log_dir=tmp_path / "logs")
    store.store(
        namespace="project:cli-index",
        title="CLI decision",
        content="record_type: decision\nclaim: Tested via CLI.",
    )
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))

    args_ok = argparse.Namespace(
        namespace="project:cli-index",
        out=None,
        max_lines=200,
        max_bytes=25600,
        format="json",
        json=True,
        fail_open=True,
    )
    rc_ok = run_compile_index(args_ok)
    assert rc_ok == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == "memory.compiled_index.v1"
    assert data["record_count"] == 1
    assert data["size_capped"] is True
