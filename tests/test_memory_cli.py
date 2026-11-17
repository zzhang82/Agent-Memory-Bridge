from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from agent_mem_bridge.cli import main
from agent_mem_bridge.deployment_config import HttpTransportConfig
from agent_mem_bridge.http_transport import build_http_app
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


def test_memory_list_hides_superseded_records_until_requested(tmp_path: Path, monkeypatch, capsys) -> None:
    from agent_mem_bridge.memory_cli import list_memories, render_memory_list

    store = _init_store(tmp_path, monkeypatch)
    original = store.store(
        namespace="project:supersede-list",
        kind="memory",
        title="Deploy to EC2 instances",
        content="record_type: decision\nclaim: Deploy to EC2 instances.",
    )
    revised = store.revise(
        original["id"],
        replacement_content="record_type: decision\nclaim: Deploy to ECS containers.",
        title="Deploy to ECS containers",
    )
    successor_id = revised["successor_id"]

    assert main(["memory", "list", "--namespace", "project:supersede-list"]) == 0
    listed = capsys.readouterr().out
    assert successor_id in listed
    assert original["id"] not in listed

    # Forgetting the successor must not resurrect the predecessor.
    assert main(["memory", "forget", successor_id, "--yes"]) == 0
    capsys.readouterr()
    assert main(["memory", "list", "--namespace", "project:supersede-list"]) == 0
    after_forget = capsys.readouterr().out
    assert "No memories found" in after_forget
    assert original["id"] not in after_forget

    report = list_memories(store, namespace="project:supersede-list", include_superseded=True)
    restored = next(item for item in report["items"] if item["id"] == original["id"])
    assert report["include_superseded"] is True
    assert restored["superseded"] is True
    assert restored["superseded_by"] == [successor_id]
    rendered = render_memory_list(report)
    assert "[superseded] Deploy to EC2 instances" in rendered

    assert main(["memory", "list", "--namespace", "project:supersede-list", "--include-superseded"]) == 0
    included = capsys.readouterr().out
    assert original["id"] in included
    assert "[superseded]" in included


def test_compile_index_help_states_minimum_ceilings(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["compile-index", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert "min: 10" in help_text
    assert "min:" in help_text and "1024" in help_text


def test_memory_list_labels_untitled_records_by_claim(tmp_path: Path, monkeypatch, capsys) -> None:
    store = _init_store(tmp_path, monkeypatch)
    store.store(
        namespace="project:untitled",
        kind="memory",
        content="record_type: decision\nclaim: Pin the schema migration to version twelve.",
    )

    assert main(["memory", "list", "--namespace", "project:untitled"]) == 0
    out = capsys.readouterr().out
    assert "Pin the schema migration to version twelve." in out
    assert "record_type: decision" not in out


def test_memory_show_marks_superseded_records_and_withholds_revise_offer(tmp_path: Path, monkeypatch, capsys) -> None:
    store = _init_store(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = repo / "deploy.json"
    tracked.write_text('{"replica_count": 2}\n', encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "cli-test@example.com")
    _git(repo, "config", "user.name", "CLI Tester")
    _git(repo, "add", "deploy.json")
    _git(repo, "commit", "-m", "initial deploy config")
    commit = _git(repo, "rev-parse", "HEAD").strip()

    original = store.store(
        namespace="project:supersede-show",
        kind="memory",
        title="Production replica count",
        content=(f"record_type: decision\ndepends_on_repo: deploy.json@{commit}\nclaim: Production runs 2 replicas."),
    )
    tracked.write_text('{"replica_count": 4}\n', encoding="utf-8")
    _git(repo, "add", "deploy.json")
    _git(repo, "commit", "-m", "scale to 4 replicas")

    revised = store.revise(
        original["id"],
        replacement_content="record_type: decision\nclaim: Production runs 4 replicas.",
        title="Production replica count updated",
    )
    monkeypatch.chdir(repo)

    assert main(["memory", "show", original["id"]]) == 0
    out = capsys.readouterr().out
    assert f"Status:       superseded (replaced by {revised['successor_id']})" in out
    assert "Live Repo Drift:" in out
    assert "Offer Revise:" not in out
    assert "Consider using 'revise'" not in out
    assert f"Already superseded by {revised['successor_id']}" in out
    assert "inspect the successor instead of revising this record." in out


def _git(root: Path, *args: str) -> str:
    import subprocess

    return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL)


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


def test_remote_memory_cli_operations_over_http(tmp_path: Path, monkeypatch, capsys) -> None:
    store = _init_store(tmp_path, monkeypatch)
    token_file = tmp_path / "test-token"
    token_file.write_text("secret-token-12345\n", encoding="utf-8")
    token_file.chmod(0o600)
    r1 = store.store(
        namespace="project:remote-test",
        kind="memory",
        title="Initial remote decision",
        content="record_type: decision\nclaim: Deploy via HTTP endpoint.",
    )
    rec_id = r1["id"]

    app = build_http_app(
        HttpTransportConfig(token_file=token_file),
        store=store,
        readiness_db_path=store.db_path,
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            url = f"http://127.0.0.1:{port}/mcp"

            # 1. Remote list text
            assert (
                main(
                    [
                        "memory",
                        "list",
                        "--url",
                        url,
                        "--token-file",
                        str(token_file),
                        "--namespace",
                        "project:remote-test",
                    ]
                )
                == 0
            )
            out = capsys.readouterr().out
            assert "Memories in project:remote-test (showing 1):" in out
            assert rec_id in out
            assert "Initial remote decision" in out

            # 2. Remote list json with query
            assert (
                main(
                    [
                        "memory",
                        "list",
                        "--url",
                        url,
                        "--token-file",
                        str(token_file),
                        "--namespace",
                        "project:remote-test",
                        "--query",
                        "HTTP",
                        "--json",
                    ]
                )
                == 0
            )
            list_payload = json.loads(capsys.readouterr().out)
            assert list_payload["count"] == 1
            assert list_payload["items"][0]["id"] == rec_id

            # 3. Remote list rejects --all across namespaces
            assert (
                main(
                    [
                        "memory",
                        "list",
                        "--url",
                        url,
                        "--token-file",
                        str(token_file),
                        "--all",
                    ]
                )
                == 2
            )
            err = capsys.readouterr().err
            assert "--all across namespaces is only supported with local database" in err

            assert (
                main(
                    [
                        "memory",
                        "list",
                        "--url",
                        url,
                        "--token-file",
                        str(token_file),
                        "--namespace",
                        "project:remote-test",
                        "--include-superseded",
                    ]
                )
                == 2
            )
            superseded_err = capsys.readouterr().err
            assert "--include-superseded is only supported with local database" in superseded_err

            # 4. Remote show text
            assert (
                main(
                    [
                        "memory",
                        "show",
                        rec_id,
                        "--url",
                        url,
                        "--token-file",
                        str(token_file),
                        "--namespace",
                        "project:remote-test",
                    ]
                )
                == 0
            )
            show_out = capsys.readouterr().out
            assert f"Memory ID:    {rec_id}" in show_out
            assert "Initial remote decision" in show_out
            assert "Deploy via HTTP endpoint." in show_out

            # 5. Remote show json
            assert (
                main(
                    [
                        "memory",
                        "show",
                        rec_id,
                        "--url",
                        url,
                        "--token-file",
                        str(token_file),
                        "--namespace",
                        "project:remote-test",
                        "--json",
                    ]
                )
                == 0
            )
            show_payload = json.loads(capsys.readouterr().out)
            assert show_payload["found"] is True
            assert show_payload["item"]["id"] == rec_id

            # 6. Remote revise
            assert (
                main(
                    [
                        "memory",
                        "revise",
                        rec_id,
                        "--url",
                        url,
                        "--token-file",
                        str(token_file),
                        "--content",
                        "record_type: decision\nclaim: Deploy via HTTP endpoint v2.",
                        "--title",
                        "Updated remote decision",
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

            assert (
                main(
                    [
                        "memory",
                        "list",
                        "--url",
                        url,
                        "--token-file",
                        str(token_file),
                        "--namespace",
                        "project:remote-test",
                        "--json",
                    ]
                )
                == 0
            )
            revised_list = json.loads(capsys.readouterr().out)
            listed_ids = [item["id"] for item in revised_list["items"]]
            assert succ_id in listed_ids
            assert rec_id not in listed_ids

            # 7. Remote forget
            assert (
                main(
                    [
                        "memory",
                        "forget",
                        succ_id,
                        "--url",
                        url,
                        "--token-file",
                        str(token_file),
                        "--yes",
                        "--json",
                    ]
                )
                == 0
            )
            forget_payload = json.loads(capsys.readouterr().out)
            assert forget_payload["deleted"] is True
            assert forget_payload["id"] == succ_id

            # 8. Remote show non-existent
            assert (
                main(
                    [
                        "memory",
                        "show",
                        "nonexistent-id",
                        "--url",
                        url,
                        "--token-file",
                        str(token_file),
                        "--namespace",
                        "project:remote-test",
                    ]
                )
                == 1
            )
            capsys.readouterr()
        finally:
            server.should_exit = True
            thread.join(10)


def test_remote_memory_cli_errors(capsys) -> None:
    # Invalid URL
    assert main(["memory", "list", "--url", "not-a-valid-url"]) == 2
    assert "HTTP endpoint must be an absolute HTTP(S) URL" in capsys.readouterr().err

    # Unreachable port
    assert main(["memory", "list", "--url", "http://127.0.0.1:1/mcp"]) == 1
    assert "remote list failed" in capsys.readouterr().err
