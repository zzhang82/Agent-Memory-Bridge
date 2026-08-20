from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_mem_bridge import first_run
from agent_mem_bridge.cli import main
from agent_mem_bridge.first_run import build_first_run_report, render_first_run_markdown
from agent_mem_bridge.onboarding import TOOL_NAMES
from agent_mem_bridge.release_contract import load_server_tool_names
from agent_mem_bridge.storage import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "project:first-use"
QUERY = "What should I check before submitting changes to this project?"


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "bridge.db", log_dir=tmp_path / "logs")


def _seed(store: MemoryStore) -> dict[str, object]:
    return store.store(
        namespace=NAMESPACE,
        kind="memory",
        title="Submission check",
        content="Run make check before submitting changes to this project.",
        tags=["domain:delivery"],
    )


def _report(store: MemoryStore) -> dict[str, object]:
    return build_first_run_report(
        store,
        client="generic",
        namespace=NAMESPACE,
        query=QUERY,
        python_path="unused",
        cwd=Path("unused"),
        bridge_home=Path("unused"),
        config_path=Path("unused"),
    )


def test_first_run_is_read_only_and_surfaces_real_durable_memory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    seeded = _seed(store)
    before = store.stats(NAMESPACE)["total_count"]
    direct_recall = store.recall(namespace=NAMESPACE, query=QUERY, kind="memory", limit=3)

    report = _report(store)
    after = store.stats(NAMESPACE)["total_count"]
    rendered = json.dumps(report, sort_keys=True)

    assert before == after == 1
    assert report["schema"] == "memory.first_run.v2"
    assert report["boundary"]["mutation_allowed"] is False
    assert report["boundary"]["memory_write_mode"] == "guided_existing_store_tool_only"
    assert report["recall"]["count"] == 1
    assert report["recall"]["items"][0]["memory_id"] == seeded["id"]
    assert "Run make check before submitting" in report["recall"]["items"][0]["summary"]
    assert direct_recall["recall_receipt"]["token"] not in rendered
    assert "recall_receipt" not in rendered
    assert "token" not in report["technical_details"]


def test_first_run_empty_state_guides_existing_store_without_writing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = store.stats(NAMESPACE)["total_count"]

    report = _report(store)
    markdown = render_first_run_markdown(report)

    assert store.stats(NAMESPACE)["total_count"] == before == 0
    assert report["remember"]["state"] == "guided_action_required"
    assert report["recall"]["count"] == 0
    assert "No suitable memory surfaced yet." in markdown
    assert "existing `store` tool" in report["remember"]["action"]


def test_first_run_default_language_is_product_friendly_and_shadow_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)

    markdown = render_first_run_markdown(_report(store))

    for heading in ("Remember", "What AMB remembered", "Why this appeared", "Feedback recorded", "Next session"):
        assert heading in markdown
    for prohibited in ("Context Compiler", "Context Attestation", "Episode Authority", "Verification Authority"):
        assert prohibited not in markdown
    assert "does not automatically rewrite memory or change ranking" in markdown
    assert "learned" not in markdown.casefold()
    assert "feedback improved" not in markdown.casefold()


def test_first_run_translates_existing_suppression_reason_metadata(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    _seed(store)
    real_task_brief = first_run.build_task_brief_report

    def with_existing_reason(*args, **kwargs):
        report = real_task_brief(*args, **kwargs)
        report["sections"]["ignored"].append({"reason_codes": ["superseded"]})
        report["sections"]["needs_review"].append({"reason_codes": ["procedure_status:unsafe"]})
        return report

    monkeypatch.setattr(first_run, "build_task_brief_report", with_existing_reason)
    report = _report(store)
    markdown = render_first_run_markdown(report)

    assert "A superseded alternative was left out." in report["explanation"]["not_used"]
    assert "An item that is not safe to use was left out." in report["explanation"]["not_used"]
    assert "What was deliberately not used:" in markdown
    assert "feedback" not in " ".join(report["explanation"]["reasons"]).casefold()


def test_first_run_cli_json_is_bounded_and_keeps_mcp_surface_unchanged(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    store = _store(tmp_path)
    _seed(store)
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_DB_PATH", str(store.db_path))
    monkeypatch.setenv("AGENT_MEMORY_BRIDGE_LOG_DIR", str(tmp_path / "logs"))

    assert main(["first-run", "--namespace", NAMESPACE, "--query", QUERY, "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema"] == "memory.first_run.v2"
    assert payload["recall"]["count"] == 1
    assert "recall_receipt" not in json.dumps(payload, sort_keys=True)
    assert load_server_tool_names(ROOT / "src" / "agent_mem_bridge" / "server.py") == TOOL_NAMES
    assert len(TOOL_NAMES) == 17
    assert "first-run" not in TOOL_NAMES


@pytest.mark.parametrize(
    ("reason_code", "expected"),
    [
        ("superseded", "A superseded alternative was left out."),
        ("procedure_status:unsafe", "An item that is not safe to use was left out."),
        ("validity:stale", "An out-of-date item was left out."),
        ("depends_on:ineligible", "An item with an ineligible dependency was left out."),
    ],
)
def test_first_run_explanation_only_translates_existing_reason_codes(
    tmp_path: Path,
    monkeypatch,
    reason_code: str,
    expected: str,
) -> None:
    store = _store(tmp_path)
    _seed(store)
    real_task_brief = first_run.build_task_brief_report

    def with_reason(*args, **kwargs):
        report = real_task_brief(*args, **kwargs)
        report["sections"]["ignored"].append({"reason_codes": [reason_code]})
        return report

    monkeypatch.setattr(first_run, "build_task_brief_report", with_reason)
    report = _report(store)

    assert expected in report["explanation"]["not_used"]
    assert all("feedback" not in reason.casefold() for reason in report["explanation"]["reasons"])
