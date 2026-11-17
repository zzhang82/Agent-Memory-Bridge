from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

from agent_mem_bridge.activation_policy import classify_task_need
from agent_mem_bridge.cli import main
from agent_mem_bridge.lifecycle_activation import (
    _memory_context,
    _one_line,
    activation_evidence_path,
    run_hook_payload,
)
from agent_mem_bridge.mcp_boundary import PUBLIC_TOOL_NAMES
from agent_mem_bridge.promotion import promote_entry
from agent_mem_bridge.repository_snapshot_store import RepositorySnapshotStore, repository_identity
from agent_mem_bridge.storage import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
MATERIAL = "The schema choice for the run ledger may be constrained by a prior project decision. PROMPT-SECRET-991"
MEMORY = "Prior decision: keep the run ledger schema constrained by the existing governance choice."
STALE_MEMORY = "Superseded decision: the old setup used a different run ledger schema."


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (
            "Start fresh on this checkout. Reply with the continuation token recorded for the listener.",
            ("recall", "fresh-session-or-handoff"),
        ),
        (
            "Add a cache for the fixture service. Reply with the storage-authority token you must preserve.",
            ("recall", "constrained-architecture-choice"),
        ),
        (
            "I am about to refresh the fixture benchmark report. Reply with the gotcha token that applies before writing it.",
            ("recall", "known-project-gotcha"),
        ),
        (
            "Continue the backup approach we settled on before this checkout. Reply with the single approach token to follow.",
            ("recall", "material-prior-context"),
        ),
        (
            "Set the fixture listener. Reply with only the current port token.",
            ("reconcile", "current-state-history-dependency"),
        ),
        (
            "Is a checksum-exception token recorded for this project? If not, say that no relevant memory was found.",
            ("recall", "recorded-project-context"),
        ),
        (
            "Which gotcha token applies before writing the fixture benchmark report?",
            ("recall", "known-project-gotcha"),
        ),
        (
            "Another client left a handoff for this checkout's project. Reply with only that handoff token.",
            ("recall", "fresh-session-or-handoff"),
        ),
    ],
)
def test_activation_policy_recognizes_frozen_material_prompt_shapes(prompt: str, expected: tuple[str, str]) -> None:
    assert classify_task_need(prompt) == expected


@pytest.mark.parametrize(
    "prompt",
    [
        "Add a cache beside the service.",
        "Set the listener port to 2222.",
        "Refresh the benchmark report.",
        "Ask another client to review this checkout.",
    ],
)
def test_activation_policy_skips_unrelated_near_misses(prompt: str) -> None:
    assert classify_task_need(prompt) == ("skip", "no-material-history-need")


@pytest.mark.parametrize(
    "prompt",
    [
        "Fix the typo in greeting.py.",
        "Update the comment punctuation in NOTES.md.",
    ],
)
def test_activation_policy_preserves_deterministic_skips(prompt: str) -> None:
    assert classify_task_need(prompt) == ("skip", "not-for-deterministic-edit")


def test_activation_policy_gives_explicit_history_precedence_over_a_typo() -> None:
    assert classify_task_need("Fix the typo after applying the recorded project exception.") == (
        "recall",
        "recorded-project-context",
    )


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def make_repo(parent: Path) -> Path:
    repo = parent / "work tree" / "fixture repo"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    return repo


def isolate(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_AUTHORITY_URL", raising=False)
    monkeypatch.delenv("AGENT_MEMORY_BRIDGE_HTTP_TOKEN_FILE", raising=False)
    home = tmp_path / "amb-home"
    home.mkdir(parents=True)
    (home / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HOME", str(home))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_CONFIG", str(home / "config.toml"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(home / "bridge.db"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(home / "logs"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_REPOSITORY_SNAPSHOT_ROOT", str(home / "repository"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_RECALL_RECEIPT_SECRET_PATH", str(home / "recall-receipt-secret.json"))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_TELEMETRY_MODE", "off")
    return home


def bind(repo: Path, home: Path, namespace: str) -> None:
    identity = repository_identity(repo)
    RepositorySnapshotStore(home / "repository").bind_namespace(namespace, identity["repository_id"])


def remember(
    home: Path,
    namespace: str,
    content: str,
    *,
    title: str = "Run ledger decision",
    tags: list[str] | None = None,
) -> None:
    store = MemoryStore(home / "bridge.db", log_dir=home / "logs")
    store.store(
        namespace=namespace,
        title=title,
        content=content,
        kind="memory",
        tags=tags or ["kind:decision"],
    )


def memory_count(home: Path) -> int:
    db = home / "bridge.db"
    if not db.exists():
        return 0
    connection = sqlite3.connect(db)
    try:
        return int(connection.execute("select count(*) from memories").fetchone()[0])
    finally:
        connection.close()


def tree_bytes(path: Path) -> bytes:
    if not path.exists():
        return b""
    chunks: list[bytes] = []
    for file in sorted(item for item in path.rglob("*") if item.is_file()):
        chunks.append(file.read_bytes())
    return b"".join(chunks)


def evidence_text(home: Path) -> str:
    path = home / "lifecycle" / "activation-evidence.jsonl"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def evidence_rows(home: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in evidence_text(home).splitlines() if line.strip()]


def assert_no_private_text(home: Path, response: dict[str, object]) -> None:
    blob = evidence_text(home) + json.dumps(response)
    for root, _dirs, files in os.walk(home):
        for name in files:
            file = Path(root) / name
            if file.suffix in {".jsonl", ".log", ".txt", ".json"} or "log" in name:
                blob += file.read_text(encoding="utf-8", errors="ignore")
    assert "PROMPT-SECRET-991" not in blob
    assert "TRANSCRIPT-SECRET" not in blob
    assert "additionalContext" not in evidence_text(home)


def task_payload(repo: Path, prompt: str, **extra: object) -> dict[str, object]:
    transcript = repo / "transcript.txt"
    transcript.write_text("TRANSCRIPT-SECRET hidden reasoning\n", encoding="utf-8")
    return {
        "kind": "task_prompt",
        "session_id": "session-1",
        "cwd": str(repo / "nested dir"),
        "prompt": prompt,
        "namespace": "project:forged",
        "client_workspace": str(repo / "forged workspace"),
        "source_client": "caller",
        **extra,
    }


def prepare_task_repo(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    home = isolate(tmp_path, monkeypatch)
    repo = make_repo(tmp_path)
    nested = repo / "nested dir"
    nested.mkdir()
    bind(repo, home, "project:fixture")
    return home, repo


def test_public_mcp_surface_does_not_grow() -> None:
    assert len(PUBLIC_TOOL_NAMES) == 17
    assert "lifecycle-hook" not in PUBLIC_TOOL_NAMES


def test_session_start_resolves_binding_without_recall_or_dump(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    remember(home, "project:fixture", MEMORY)
    (repo / "UNTRACKED.txt").write_text("dirty\n", encoding="utf-8")
    bindings = (home / "repository" / "bindings.json").read_bytes()
    logs = tree_bytes(home / "logs")
    before = memory_count(home)
    response = run_hook_payload(
        {
            "kind": "session_start",
            "source": "startup",
            "session_id": "session-1",
            "cwd": str(repo / "nested dir"),
            "prompt": MATERIAL,
            "namespace": "project:forged",
            "client_workspace": "forged",
        }
    )
    row = evidence_rows(home)[-1]
    assert response == {"decision": "skip", "context": ""}
    assert row["resolution_status"] == "bound"
    assert row["namespace"] == "project:fixture"
    assert row["ignored_caller_scope"] is True
    assert row["decision"] == "skip"
    assert row["rule_id"] == "session-entry-no-dump"
    assert row["recall_invoked"] is False
    assert row["adapter_loaded"] is True
    assert row["host"] == "core"
    assert row["kind"] == "session_start"
    assert memory_count(home) == before
    assert (home / "repository" / "bindings.json").read_bytes() == bindings
    assert tree_bytes(home / "logs") == logs
    assert_no_private_text(home, response)


def test_negative_controls_do_not_recall(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    remember(home, "project:fixture", MEMORY)
    before = memory_count(home)
    typo = run_hook_payload(task_payload(repo, "Fix the misspelled import in this file. PROMPT-SECRET-991"))
    optional = run_hook_payload(
        task_payload(
            repo, "There are several alternatives for this refactor. PROMPT-SECRET-991", session_id="session-2"
        )
    )
    rows = evidence_rows(home)
    assert typo == {"decision": "skip", "context": ""}
    assert optional == {"decision": "skip", "context": ""}
    assert rows[-2]["rule_id"] == "not-for-deterministic-edit"
    assert rows[-1]["rule_id"] == "large-design-choice"
    assert all(row["recall_invoked"] is False for row in rows)
    assert memory_count(home) == before


def test_material_recall_distinguishes_hit_no_hit_irrelevant_and_repeat(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    remember(home, "project:fixture", MEMORY)
    before = memory_count(home)
    hit = run_hook_payload(task_payload(repo, MATERIAL))
    logs_after_hit = tree_bytes(home / "logs")
    repeated = run_hook_payload(task_payload(repo, MATERIAL))
    assert tree_bytes(home / "logs") == logs_after_hit
    changed = run_hook_payload(
        task_payload(repo, MATERIAL + " The governance migration also changed.", session_id="session-1")
    )
    assert "Untrusted governed context" in changed["context"]
    hit_row, repeat_row, changed_row = evidence_rows(home)[-3:]
    assert hit_row["recall_invoked"] is True
    assert hit_row["recall_state"] == "hit"
    assert hit_row["namespace"] == "project:fixture"
    assert "Untrusted governed context" in hit["context"]
    assert "PROMPT-SECRET-991" not in hit["context"]
    assert repeat_row["repeat_suppressed"] is True
    assert repeat_row["recall_invoked"] is False
    assert repeated == {"decision": "skip", "context": ""}
    assert changed_row["recall_invoked"] is True
    assert memory_count(home) == before
    assert_no_private_text(home, hit)

    empty_home = isolate(tmp_path / "empty", monkeypatch)
    empty_repo = make_repo(tmp_path / "empty-repo")
    (empty_repo / "nested dir").mkdir()
    bind(empty_repo, empty_home, "project:fixture")
    MemoryStore(empty_home / "bridge.db", log_dir=empty_home / "logs")
    no_hit = run_hook_payload(task_payload(empty_repo, MATERIAL, session_id="empty-session"))
    no_hit_row = evidence_rows(empty_home)[-1]
    assert no_hit_row["recall_state"] == "no_hit"
    assert no_hit_row["recall_invoked"] is True
    assert "not an unavailable bridge" in no_hit["context"]

    unrelated_home = isolate(tmp_path / "unrelated", monkeypatch)
    unrelated_repo = make_repo(tmp_path / "unrelated-repo")
    (unrelated_repo / "nested dir").mkdir()
    bind(unrelated_repo, unrelated_home, "project:fixture")
    remember(
        unrelated_home,
        "project:fixture",
        "Banana pancake recipe for the office kitchen.",
        title="Kitchen note",
    )
    irrelevant = run_hook_payload(task_payload(unrelated_repo, MATERIAL, session_id="unrelated-session"))
    irrelevant_row = evidence_rows(unrelated_home)[-1]
    assert irrelevant_row["recall_state"] == "irrelevant_hit"
    assert "banana" not in irrelevant["context"].lower()
    assert "not an unavailable bridge" in irrelevant["context"]


def test_missing_local_database_is_not_reported_as_amb_unavailable(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    response = run_hook_payload(task_payload(repo, MATERIAL))
    row = evidence_rows(home)[-1]
    context = response["context"]
    assert row["authority_mode"] == "unknown"
    assert row["availability"] == "unknown"
    assert row["recall_state"] == "skipped"
    assert row["recall_invoked"] is False
    assert row["rule_id"] == "authority-unknown"
    assert not (home / "bridge.db").exists()
    assert "not an unavailable bridge" in context
    assert "not an empty memory result" in context
    assert "AMB was unavailable" not in context
    assert "no matching memory" not in context


@contextmanager
def remote_authority(tmp_path: Path, monkeypatch, *, token_file=None):
    from agent_mem_bridge.deployment_config import HttpTransportConfig
    from agent_mem_bridge.http_transport import build_http_app

    store = MemoryStore(tmp_path / "remote.db", log_dir=tmp_path / "remote-logs")
    calls = []
    original = store.recall

    def observed(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(store, "recall", observed)
    app = build_http_app(HttpTransportConfig(token_file=token_file), store=store, readiness_db_path=store.db_path)
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
            yield f"http://127.0.0.1:{port}/mcp", store, calls
        finally:
            server.should_exit = True
            thread.join(10)
            assert not thread.is_alive()


@pytest.mark.parametrize("configured_by", ["env", "config"])
@pytest.mark.parametrize("scenario", ["hit", "no_hit", "skip", "unavailable", "irrelevant_hit", "stale_conflict"])
def test_remote_authority_does_not_open_a_stale_local_database(
    tmp_path: Path, monkeypatch, configured_by, scenario
) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    trap = "Prior decision: replace the run ledger schema with CONFLICTING-LOCAL-TRAP."
    remember(home, "project:fixture", trap)
    attempts = []
    original_connect = sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        if str(home / "bridge.db") in str(database):
            attempts.append(str(database))
            raise AssertionError("local database opened despite remote selection")
        return original_connect(database, *args, **kwargs)

    with remote_authority(tmp_path, monkeypatch) as (url, store, calls):
        if scenario in {"hit", "stale_conflict", "irrelevant_hit"}:
            store.store(
                namespace="project:fixture",
                kind="memory",
                content=(
                    STALE_MEMORY
                    if scenario == "stale_conflict"
                    else "Banana pancake recipe"
                    if scenario == "irrelevant_hit"
                    else MEMORY
                ),
            )
        if scenario == "irrelevant_hit":
            monkeypatch.setattr("agent_mem_bridge.lifecycle_activation._recall_query", lambda _: "banana")
        if scenario == "unavailable":
            url = "http://127.0.0.1:1/mcp"
        if configured_by == "env":
            monkeypatch.setenv("AGENT_MEMORY_BRIDGE_AUTHORITY_URL", url)
        else:
            (home / "config.toml").write_text(f'[deployment]\nauthority_url = "{url}"\n', encoding="utf-8")
        monkeypatch.setattr(sqlite3, "connect", guarded_connect)
        transport_attempts = []
        if scenario == "skip":

            def forbidden_transport(*args, **kwargs):
                transport_attempts.append(1)
                raise AssertionError("negative control created an HTTP client")

            monkeypatch.setattr("agent_mem_bridge.lifecycle_http.httpx2.AsyncClient", forbidden_transport)
        prompt = "Fix the typo" if scenario == "skip" else MATERIAL
        response = run_hook_payload(task_payload(repo, prompt))
        row = evidence_rows(home)[-1]
        assert row["authority_mode"] == "remote"
        assert row["recall_state"] == {"skip": "skipped", "unavailable": "error"}.get(scenario, scenario)
        assert len(calls) == (0 if scenario in {"skip", "unavailable"} else 1)
        if calls:
            assert calls[0]["namespace"] == "project:fixture"
            assert calls[0]["limit"] == 3
            assert calls[0]["kind"] == "memory"
            run_hook_payload(task_payload(repo, prompt))
            assert evidence_rows(home)[-1]["repeat_suppressed"] is True
            assert len(calls) == 1
        assert attempts == []  # Independent of hook exception handling.
        assert transport_attempts == []
        assert "CONFLICTING-LOCAL-TRAP" not in json.dumps(response)
        assert url not in evidence_text(home)
        assert set(response) == {"decision", "context"}
        if scenario == "unavailable":
            assert row["availability"] == "error"
            assert "not an empty memory result" in json.dumps(response)


@pytest.mark.parametrize("authorized", [True, False])
def test_remote_bearer_authentication(tmp_path: Path, monkeypatch, authorized) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    remember(home, "project:fixture", "CONFLICTING-LOCAL-TRAP run ledger schema")
    token = tmp_path / "token"
    token.write_text("private-fixture-token", encoding="ascii")
    token.chmod(0o600)
    with remote_authority(tmp_path, monkeypatch, token_file=token) as (url, store, calls):
        store.store(namespace="project:fixture", kind="memory", content=MEMORY)
        monkeypatch.setenv("AGENT_MEMORY_BRIDGE_AUTHORITY_URL", url)
        if authorized:
            monkeypatch.setenv("AGENT_MEMORY_BRIDGE_HTTP_TOKEN_FILE", str(token))
        local_calls = []

        def forbidden(*args, **kwargs):
            local_calls.append(1)
            raise AssertionError("local fallback")

        monkeypatch.setattr("agent_mem_bridge.lifecycle_activation._recall", forbidden)
        response = run_hook_payload(task_payload(repo, MATERIAL))
        assert evidence_rows(home)[-1]["recall_state"] == ("hit" if authorized else "error")
        assert len(calls) == int(authorized)
        assert local_calls == []
        assert "private-fixture-token" not in json.dumps(response) + evidence_text(home)


def test_exact_repeat_is_suppressed_but_a_paraphrase_recalls_again(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    remember(home, "project:fixture", MEMORY)
    first = "The schema choice for the run ledger may be constrained by a prior project decision."
    paraphrase = "A prior project decision may still constrain the run ledger schema choice."
    run_hook_payload(task_payload(repo, first, session_id="same-need"))
    repeated = run_hook_payload(task_payload(repo, first, session_id="same-need"))
    changed = run_hook_payload(task_payload(repo, paraphrase, session_id="same-need"))
    first_row, repeat_row, paraphrase_row = evidence_rows(home)[-3:]
    assert first_row["recall_invoked"] is True
    assert repeat_row["repeat_suppressed"] is True
    assert repeat_row["rule_id"] == "exact-prompt-repeat"
    assert repeat_row["recall_invoked"] is False
    assert repeated == {"decision": "skip", "context": ""}
    assert paraphrase_row["recall_invoked"] is True
    assert paraphrase_row["repeat_suppressed"] is False
    assert "Untrusted governed context" in changed["context"]


def test_scope_failures_do_not_mutate_bindings_or_accept_caller_namespace(tmp_path: Path, monkeypatch) -> None:
    home = isolate(tmp_path, monkeypatch)
    repo = make_repo(tmp_path)
    (repo / "nested dir").mkdir()
    missing = home / "repository" / "bindings.json"
    unbound = run_hook_payload(task_payload(repo, MATERIAL))
    assert evidence_rows(home)[-1]["resolution_status"] == "no_binding"
    assert evidence_rows(home)[-1]["rule_id"] == "no-project-binding"
    assert evidence_rows(home)[-1]["namespace"] is None
    assert not missing.exists()
    assert "no governed project binding" in unbound["context"]

    bind(repo, home, "project:one")
    bind(repo, home, "project:two")
    before = (home / "repository" / "bindings.json").read_bytes()
    ambiguous = run_hook_payload(task_payload(repo, MATERIAL, session_id="other-session"))
    row = evidence_rows(home)[-1]
    assert row["resolution_status"] == "ambiguous_binding"
    assert row["decision"] == "fail_closed"
    assert row["recall_invoked"] is False
    assert "project:forged" not in evidence_text(home)
    assert (home / "repository" / "bindings.json").read_bytes() == before
    assert "ambiguous" in ambiguous["context"]


def test_stale_memory_is_labeled_and_not_treated_as_authority(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    remember(home, "project:fixture", STALE_MEMORY, tags=["kind:decision", "lifecycle:stale"])
    response = run_hook_payload(
        task_payload(repo, "The old setup looks superseded and conflicts with the current server. PROMPT-SECRET-991")
    )
    row = evidence_rows(home)[-1]
    context = response["context"]
    assert row["recall_state"] == "stale_conflict"
    assert row["stale_ids"]
    assert "not durable authority" in context
    assert "Do not apply it over the live repository." in context
    assert "Read the relevant current repository files before acting or answering" in context
    assert memory_count(home) == 1


def test_memory_context_allows_nonstale_historical_answers_without_repository_duplication() -> None:
    context = _memory_context(
        [
            {
                "id": "memory-historical-7",
                "title": "Migration note",
                "content": "In 2018 the archive moved from cedar to juniper.",
            }
        ],
        "abc1234",
        stale=False,
        reconcile=False,
    )

    assert "Untrusted governed context" in context
    assert "Live repository evidence wins" in context
    assert "archive moved from cedar to juniper" in context
    assert "may be answered from AMB with recalled-record provenance" in context
    assert "absence from current repository files alone is not a contradiction" in context
    assert "independently verified current runtime value" in context
    assert "Inspect relevant repository files before acting or answering about current state." in context
    assert "reconcile this context with that inspected evidence" not in context


@pytest.mark.parametrize("stale,reconcile", [(True, False), (False, True)])
def test_memory_context_keeps_stale_and_reconcile_refusal(stale: bool, reconcile: bool) -> None:
    context = _memory_context(
        [{"id": "memory-control-3", "title": "Old setting", "content": "Use a retired setting."}],
        "def5678",
        stale=stale,
        reconcile=reconcile,
    )

    assert "Live repository evidence wins" in context
    assert "Do not apply it over the live repository." in context
    assert "Read the relevant current repository files before acting or answering" in context
    assert "reconcile this context with that inspected evidence" in context
    assert "absence from current repository files alone is not a contradiction" not in context


def test_memory_context_truncates_on_word_boundaries_and_marks_recheck() -> None:
    long_title = "Confirm the deployment window before touching production " * 4
    long_content = "The rollout waits for the maintenance window and the backup " * 12
    context = _memory_context(
        [
            {
                "id": "memory-recheck-1",
                "title": long_title,
                "content": long_content,
                "recheck_status": "needs_recheck",
                "recheck_reasons": ["depends_on:superseded:memory-9", "depends_on_repo:changed:deploy.json"],
            },
            {
                "id": "memory-current-2",
                "title": "Current decision",
                "content": "Keep the current rollout decision.",
                "recheck_status": "current",
                "recheck_reasons": [],
            },
        ],
        "abc1234",
        stale=False,
        reconcile=False,
    )

    title_line = next(line for line in context.splitlines() if line.startswith("- Confirm"))
    assert title_line.startswith("- ")
    assert " [needs_recheck: depends_on:superseded:memory-9, depends_on_repo:changed:deploy.json]" in title_line
    assert _one_line(long_title, 120).endswith("...")
    assert len(_one_line(long_title, 120)) <= 120
    assert len(_one_line(long_content, 280)) <= 280
    assert not _one_line(long_content, 280).removesuffix("...").endswith("maintenan")
    assert "- Current decision: Keep the current rollout decision." in context
    assert (
        "Note: items marked [needs_recheck] have unresolved or changed dependencies; verify before applying." in context
    )
    assert "may be answered from AMB with recalled-record provenance" in context


def test_compaction_preserves_continuity_without_transcript_or_recall(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    remember(home, "project:fixture", MEMORY)
    before = memory_count(home)
    logs = tree_bytes(home / "logs")
    compact = run_hook_payload(
        {
            "kind": "compaction",
            "session_id": "session-1",
            "cwd": str(repo),
            "prompt": MATERIAL,
        }
    )
    resumed = run_hook_payload(
        {
            "kind": "session_start",
            "source": "compact",
            "session_id": "session-1",
            "cwd": str(repo),
            "prompt": MATERIAL,
        }
    )
    ignored_vendor = run_hook_payload(
        {
            "hook_event_name": "PreCompact",
            "trigger": "auto",
            "session_id": "session-1",
            "cwd": str(repo),
            "prompt": MATERIAL,
            "transcript_path": str(repo / "transcript.txt"),
        }
    )
    rows = evidence_rows(home)
    assert compact["decision"] == "continuity"
    assert "not a transcript" in compact["context"]
    assert "project:fixture" in compact["context"]
    assert MEMORY not in compact["context"]
    assert resumed["decision"] == "continuity"
    assert "not a transcript" in resumed["context"]
    assert ignored_vendor == {"decision": "ignore", "context": ""}
    assert rows[-1]["kind"] == "ignore"
    assert memory_count(home) == before
    assert tree_bytes(home / "logs") == logs
    assert_no_private_text(home, resumed)


def test_recall_error_is_not_a_no_hit(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    (home / "bridge.db").write_text("not a database", encoding="utf-8")
    response = run_hook_payload(task_payload(repo, MATERIAL))
    row = evidence_rows(home)[-1]
    assert row["recall_state"] == "error"
    assert row["recall_invoked"] is True
    assert row["error_type"]
    assert "not an empty memory result" in response["context"]


def test_cli_hook_entrypoint_handles_spaced_cwd_and_invalid_input(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    remember(home, "project:fixture", MEMORY)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "agent_mem_bridge", "lifecycle-hook"],
        input=json.dumps(task_payload(repo, MATERIAL, session_id="cli-session")),
        text=True,
        cwd=repo / "nested dir",
        env=env,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert "Untrusted governed context" in payload["context"]
    assert "PROMPT-SECRET-991" not in completed.stdout
    assert evidence_rows(home)[-1]["resolution_status"] == "bound"
    assert activation_evidence_path() == home / "lifecycle" / "activation-evidence.jsonl"

    monkeypatch.setattr(sys, "stdin", io.StringIO("not-json"))
    assert main(["lifecycle-hook"]) == 0


def _stop_artifact() -> dict[str, object]:
    return {
        "schema": "memory.visible_artifact.v1",
        "artifact_id": "decision-1",
        "artifact_class": "decision",
        "claim": "Keep write-side capture in the hidden review lane until a person promotes it.",
        "reason": "The rule is reusable for later sessions and must not become trusted memory automatically.",
        "evidence_refs": ["visible:codex-stop:decision-1"],
        "scope": "project",
        "namespace": "project:forged",
        "candidate_status": "approved",
        "decision": "allow",
    }


def _capture_payload(repo: Path, artifact: object, **extra: object) -> dict[str, object]:
    transcript = repo / "transcript.txt"
    transcript.write_text("TRANSCRIPT-SECRET hidden reasoning\n", encoding="utf-8")
    return {
        "kind": "capture",
        "session_id": "thr_123",
        "turn_id": "turn_123",
        "cwd": str(repo / "nested dir"),
        "visible_artifact": artifact,
        "namespace": "project:forged",
        **extra,
    }


def _run_cli(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "agent_mem_bridge", "lifecycle-hook"],
        input=json.dumps(payload),
        text=True,
        env=env,
        capture_output=True,
        check=False,
    )


def _learning_rows(home: Path) -> list[sqlite3.Row]:
    db = home / "bridge.db"
    if not db.exists():
        return []
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        return list(
            connection.execute(
                """
                SELECT id, namespace, content, tags_json, is_learning_candidate
                FROM memories
                WHERE COALESCE(is_learning_candidate, 0) = 1
                ORDER BY created_at
                """
            )
        )
    finally:
        connection.close()


def test_codex_stop_structured_artifact_creates_hidden_needs_review_candidate(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    explicit = "Explicit user store remains visible beside lifecycle capture."
    remember(home, "project:fixture", explicit, title="Explicit store")
    artifact = _stop_artifact()
    completed = _run_cli(_capture_payload(repo, artifact))
    assert completed.returncode == 0, completed.stderr
    response = json.loads(completed.stdout)
    assert response == {"decision": "skip", "context": ""}
    assert "hidden review lane" not in completed.stdout
    assert "project:forged" not in completed.stdout

    rows = _learning_rows(home)
    assert len(rows) == 1
    row = rows[0]
    assert row["namespace"] == "project:fixture"
    assert row["is_learning_candidate"] == 1
    assert "candidate_status: needs_review" in row["content"]
    assert "capture_boundary: post_run" in row["content"]
    assert "source_runtime: lifecycle" in row["content"]
    assert "source_session_id: thr_123" in row["content"]
    assert "source_task_id: turn_123" in row["content"]
    assert "visible_artifact_id: decision-1" in row["content"]
    digest = hashlib.sha256(json.dumps(artifact, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert f"lifecycle:sha256:{digest}" in row["content"]
    receipt = json.loads((home / "lifecycle" / "capture-evidence.jsonl").read_text())
    assert receipt["schema"] == "amb.lifecycle-capture-evidence.v1"
    assert "hook_event_name" not in receipt
    assert receipt["writes"] == 1
    assert receipt["automatic_promotion"] is False
    assert receipt["record_ids"] == [row["id"]]
    assert receipt["artifact_sha256"] == digest
    assert "approved" not in row["content"]
    assert "project:forged" not in row["content"]
    assert "TRANSCRIPT-SECRET" not in row["content"]
    assert "candidate_status:needs_review" in row["tags_json"]
    assert_no_private_text(home, response)

    store = MemoryStore(home / "bridge.db", log_dir=home / "logs")
    normal = store.recall(namespace="project:fixture", query="hidden review lane", limit=10)
    explicit_recall = store.recall(namespace="project:fixture", query="Explicit user store remains visible", limit=10)
    review = store.recall(namespace="project:fixture", tags_any=["kind:learning-candidate"], limit=10)
    forged = store.recall(namespace="project:forged", query="hidden review lane", limit=10)
    assert normal["count"] == 0
    assert explicit_recall["count"] == 1
    assert review["count"] == 1
    assert forged["count"] == 0
    with pytest.raises(ValueError, match="cannot be promoted directly"):
        promote_entry(store, str(row["id"]), "learn")

    repeated = _run_cli(_capture_payload(repo, artifact))
    assert repeated.returncode == 0, repeated.stderr
    assert len(_learning_rows(home)) == 1
    assert memory_count(home) == 2


def test_codex_stop_summary_and_wrapped_artifact_capture_nothing(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    remember(home, "project:fixture", MEMORY)
    before = memory_count(home)
    summary = (
        "Summary: Keep write-side capture in the hidden review lane until a person promotes it. "
        "The rule is reusable for later sessions."
    )
    wrapped = "Captured decision:\n```json\n" + json.dumps(_stop_artifact()) + "\n```"
    for message in (summary, wrapped):
        completed = _run_cli(_capture_payload(repo, message))
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout) == {"decision": "skip", "context": ""}
    assert _learning_rows(home) == []
    assert memory_count(home) == before
    receipts = [json.loads(line) for line in (home / "lifecycle" / "capture-evidence.jsonl").read_text().splitlines()]
    assert len(receipts) == 2
    assert all(row["writes"] == 0 and row["disposition"] == "no_capture" for row in receipts)
    assert all(row["schema"] == "amb.lifecycle-capture-evidence.v1" for row in receipts)
    assert all("hook_event_name" not in row for row in receipts)
    assert "TRANSCRIPT-SECRET" not in evidence_text(home)


def test_codex_precompact_transcript_does_not_capture(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    transcript = repo / "transcript.txt"
    transcript.write_text(json.dumps(_stop_artifact()) + "\nTRANSCRIPT-SECRET\n", encoding="utf-8")
    compaction_response = run_hook_payload(
        {
            "kind": "compaction",
            "session_id": "thr_123",
            "turn_id": "turn_123",
            "cwd": str(repo / "nested dir"),
            "visible_artifact": _stop_artifact(),
        }
    )
    assert compaction_response["decision"] == "continuity"
    vendor_response = run_hook_payload(
        {
            "kind": "capture",
            "session_id": "thr_123",
            "turn_id": "turn_123",
            "cwd": str(repo / "nested dir"),
            "transcript_path": str(transcript),
            "visible_artifact": _stop_artifact(),
        }
    )
    assert vendor_response == {"decision": "ignore", "context": ""}
    assert not (home / "bridge.db").exists()
    assert _learning_rows(home) == []


@pytest.mark.parametrize(
    "override",
    [
        {"visible_artifact": None},
        {"visible_artifact": "{"},
        {"visible_artifact": {"schema": "wrong"}},
        {"visible_artifact": {**_stop_artifact(), "claim": "界" * 1400}},
        {"visible_artifact": {**_stop_artifact(), "hidden_reasoning": "not retained"}},
        {"session_id": ""},
        {"turn_id": ""},
        {"host": "opencode"},
        {"last_assistant_message": "vendor"},
    ],
)
def test_stop_rejects_unsafe_missing_or_wrong_host_input(tmp_path: Path, monkeypatch, override) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    remember(home, "project:fixture", MEMORY)
    payload = _capture_payload(repo, _stop_artifact())
    payload.update(override)
    response = run_hook_payload(payload)
    assert response["decision"] in {"skip", "ignore"}
    assert _learning_rows(home) == []
    assert memory_count(home) == 1


def test_stop_capture_failure_is_not_a_passing_negative_control(tmp_path: Path, monkeypatch) -> None:
    home, repo = prepare_task_repo(tmp_path, monkeypatch)
    (home / "bridge.db").write_text("not sqlite")
    assert run_hook_payload(_capture_payload(repo, _stop_artifact())) == {"decision": "skip", "context": ""}
    receipt = json.loads((home / "lifecycle" / "capture-evidence.jsonl").read_text())
    assert receipt["schema"] == "amb.lifecycle-capture-evidence.v1"
    assert "hook_event_name" not in receipt
    assert receipt["disposition"] == "error"
    assert receipt["writes"] is None
    assert receipt["automatic_promotion"] is None
    assert "TRANSCRIPT-SECRET" not in evidence_text(home)


def test_codex_stop_does_not_capture_without_binding_or_local_authority(tmp_path: Path, monkeypatch) -> None:
    home = isolate(tmp_path, monkeypatch)
    repo = make_repo(tmp_path)
    (repo / "nested dir").mkdir()
    unbound = run_hook_payload(_capture_payload(repo, _stop_artifact()))
    assert unbound == {"decision": "skip", "context": ""}
    assert not (home / "bridge.db").exists()

    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    home, repo = prepare_task_repo(remote_root, monkeypatch)
    remember(home, "project:fixture", MEMORY)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_AUTHORITY_URL", "https://amb.example/mcp")

    def fail_if_opened(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("local database opened despite a remote authority")

    monkeypatch.setattr("agent_mem_bridge.storage.MemoryStore", fail_if_opened)
    remote = run_hook_payload(_capture_payload(repo, _stop_artifact()))
    assert remote == {"decision": "skip", "context": ""}
    assert _learning_rows(home) == []
