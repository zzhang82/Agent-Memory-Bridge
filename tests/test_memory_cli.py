from __future__ import annotations

import json
from pathlib import Path

from agent_mem_bridge.cli import main
from agent_mem_bridge.storage import MemoryStore


def _init_store(tmp_path: Path, monkeypatch) -> MemoryStore:
    db_path = tmp_path / "bridge.db"
    log_dir = tmp_path / "logs"
    store = MemoryStore(db_path, log_dir=log_dir)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_REPOSITORY_SNAPSHOT_ROOT", str(tmp_path / "snapshots"))
    return store


def test_memory_no_subcommand_exits_with_help(tmp_path: Path, monkeypatch, capsys) -> None:
    _init_store(tmp_path, monkeypatch)
    assert main(["memory"]) == 2
    out, err = capsys.readouterr()
    assert "agent-memory-bridge memory" in err


def test_memory_list_empty(tmp_path: Path, monkeypatch, capsys) -> None:
    _init_store(tmp_path, monkeypatch)
    assert main(["memory", "list", "--namespace", "project:test"]) == 0
    out = capsys.readouterr().out
    assert "No memories found in project:test." in out

    assert main(["memory", "list", "--namespace", "project:test", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 0
    assert payload["items"] == []
    assert payload["namespace"] == "project:test"


def test_memory_list_with_records_and_filters(tmp_path: Path, monkeypatch, capsys) -> None:
    store = _init_store(tmp_path, monkeypatch)
    m1 = store.store(
        namespace="project:alpha",
        kind="memory",
        title="First decision",
        content="record_type: decision\nclaim: Pin Python to 3.12.",
        tags=["python", "version"],
    )
    m2 = store.store(
        namespace="project:alpha",
        kind="memory",
        title="Second decision",
        content="record_type: decision\nclaim: Use SQLite WAL mode for storage.",
        tags=["sqlite"],
    )
    store.store(
        namespace="project:beta",
        kind="memory",
        title="Beta note",
        content="Different project note.",
    )

    # Scoped to project:alpha
    assert main(["memory", "list", "--namespace", "project:alpha"]) == 0
    out = capsys.readouterr().out
    assert "Memories in project:alpha (showing 2):" in out
    assert m1["id"] in out
    assert m2["id"] in out
    assert "First decision" in out
    assert "Second decision" in out

    # Filter with query
    assert main(["memory", "list", "--namespace", "project:alpha", "--query", "SQLite"]) == 0
    out = capsys.readouterr().out
    assert "Second decision" in out
    assert "First decision" not in out

    # Limit
    assert main(["memory", "list", "--namespace", "project:alpha", "--limit", "1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert len(payload["items"]) == 1

    # All namespaces
    assert main(["memory", "list", "--all", "--json"]) == 0
    all_payload = json.loads(capsys.readouterr().out)
    assert all_payload["count"] == 3
    assert all_payload["all_namespaces"] is True


def test_memory_show_and_tombstone(tmp_path: Path, monkeypatch, capsys) -> None:
    store = _init_store(tmp_path, monkeypatch)
    record = store.store(
        namespace="project:show-test",
        kind="memory",
        title="Configuration rule",
        content="record_type: decision\nclaim: Store tokens in environment variables.",
        tags=["security", "tokens"],
    )
    rec_id = record["id"]

    # Show existing
    assert main(["memory", "show", rec_id]) == 0
    out = capsys.readouterr().out
    assert f"Memory ID:    {rec_id}" in out
    assert "Namespace:    project:show-test" in out
    assert "Title:        Configuration rule" in out
    assert "Tags:         [security, tokens]" in out or "security" in out
    assert "Content:" in out
    assert "Store tokens in environment variables." in out

    # Show existing as JSON
    assert main(["memory", "show", rec_id, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["found"] is True
    assert payload["forgotten"] is False
    assert payload["item"]["id"] == rec_id

    # Show non-existent
    assert main(["memory", "show", "non-existent-id"]) == 1
    err = capsys.readouterr().err
    assert "record not found" in err

    assert main(["memory", "show", "non-existent-id", "--json"]) == 1
    json_err = json.loads(capsys.readouterr().out)
    assert json_err["found"] is False


def test_memory_forget_flow(tmp_path: Path, monkeypatch, capsys) -> None:
    store = _init_store(tmp_path, monkeypatch)
    record = store.store(
        namespace="project:forget-test",
        kind="memory",
        title="Temporary note",
        content="This note should be forgotten.",
    )
    rec_id = record["id"]

    # Forget with interactive prompt aborted
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert main(["memory", "forget", rec_id]) == 1
    out = capsys.readouterr().out
    assert "Aborted." in out

    # Forget with --yes
    assert main(["memory", "forget", rec_id, "--yes"]) == 0
    out = capsys.readouterr().out
    assert f"Forgotten memory {rec_id}" in out

    # Show now reports forgotten tombstone
    assert main(["memory", "show", rec_id]) == 0
    show_out = capsys.readouterr().out
    assert f"Record {rec_id} has been forgotten." in show_out
    assert "Deleted At:" in show_out

    # Forgetting again reports already forgotten
    assert main(["memory", "forget", rec_id, "--yes"]) == 0
    out_already = capsys.readouterr().out
    assert "has already been forgotten" in out_already

    # Forget non-existent
    assert main(["memory", "forget", "unknown-id", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "memory not found" in err


def test_memory_revise_flow(tmp_path: Path, monkeypatch, capsys) -> None:
    store = _init_store(tmp_path, monkeypatch)
    record = store.store(
        namespace="project:revise-test",
        kind="memory",
        title="Initial architecture",
        content="record_type: decision\nclaim: Deploy to EC2 instances.",
    )
    rec_id = record["id"]

    # Revise with prompt aborted
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert (
        main(
            [
                "memory",
                "revise",
                rec_id,
                "--content",
                "record_type: decision\nclaim: Deploy to ECS containers.",
            ]
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "Aborted." in out

    # Revise with --yes
    assert (
        main(
            [
                "memory",
                "revise",
                rec_id,
                "--content",
                "record_type: decision\nclaim: Deploy to ECS containers.",
                "--title",
                "Updated architecture",
                "--tag",
                "containers",
                "--yes",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert f"Revised memory {rec_id}." in out
    assert "Successor ID:" in out
    assert "Updated architecture" in out
    succ_id = [line.split(":", 1)[1].strip() for line in out.splitlines() if "Successor ID:" in line][0]

    # Verify via show that successor record supersedes the old record
    assert main(["memory", "show", succ_id, "--json"]) == 0
    succ_data = json.loads(capsys.readouterr().out)["item"]
    assert succ_data["relations"]["supersedes"] == [rec_id]

    # Revise non-existent
    assert main(["memory", "revise", "unknown-id", "--content", "foo", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "memory not found" in err

    # Forget the successor then try to revise it -> should fail
    assert main(["memory", "forget", succ_id, "--yes"]) == 0
    capsys.readouterr()

    assert main(["memory", "revise", succ_id, "--content", "bar", "--yes"]) == 1
    err2 = capsys.readouterr().err
    assert "has been forgotten and cannot be revised" in err2


def test_memory_revise_rejects_reserved_policy_tags(tmp_path: Path, monkeypatch, capsys) -> None:
    store = _init_store(tmp_path, monkeypatch)
    record = store.store(
        namespace="project:policy-test",
        kind="memory",
        content="Valid content.",
    )
    rec_id = record["id"]

    assert (
        main(
            [
                "memory",
                "revise",
                rec_id,
                "--content",
                "Replacement content.",
                "--tag",
                "kind:signal",
                "--yes",
            ]
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "reserved policy tags" in err


def test_memory_json_flag_for_forget_and_revise(tmp_path: Path, monkeypatch, capsys) -> None:
    store = _init_store(tmp_path, monkeypatch)
    record = store.store(
        namespace="project:json-test",
        kind="memory",
        title="Note for json testing",
        content="Testing json output modes.",
    )
    rec_id = record["id"]

    # Revise with --json
    assert (
        main(
            [
                "memory",
                "revise",
                rec_id,
                "--content",
                "Updated json content.",
                "--title",
                "Updated json title",
                "--yes",
                "--json",
            ]
        )
        == 0
    )
    rev_payload = json.loads(capsys.readouterr().out)
    assert rev_payload["revised"] is True
    assert rev_payload["predecessor_id"] == rec_id
    succ_id = rev_payload["successor_id"]
    assert rev_payload["item"]["title"] == "Updated json title"

    # Forget with --json
    assert main(["memory", "forget", succ_id, "--yes", "--json"]) == 0
    forget_payload = json.loads(capsys.readouterr().out)
    assert forget_payload["deleted"] is True
    assert forget_payload["id"] == succ_id
    assert forget_payload["tombstone"]["namespace"] == "project:json-test"


def test_memory_list_default_namespace_bound_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    from tests.test_project_init import make_repo

    store = _init_store(tmp_path, monkeypatch)
    repo = make_repo(tmp_path, name="SampleProject")
    bound_ns = "project:sample-project"
    assert main(["project", "init", str(repo), "--namespace", bound_ns, "--yes"]) == 0
    capsys.readouterr()

    store.store(
        namespace=bound_ns,
        kind="memory",
        title="Bound note",
        content="Bound project fact.",
    )

    monkeypatch.chdir(repo)
    assert main(["memory", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["namespace"] == bound_ns
    assert payload["count"] == 1
    assert payload["items"][0]["title"] == "Bound note"
