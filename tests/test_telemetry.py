import json
from pathlib import Path

from agent_mem_bridge.storage import MemoryStore
from agent_mem_bridge.telemetry import Telemetry, TelemetryConfig


def _read_spans(log_dir: Path) -> list[dict]:
    log_path = log_dir / "spans.jsonl"
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_disabled_telemetry_writes_no_span_file(tmp_path: Path) -> None:
    telemetry = Telemetry(TelemetryConfig(mode="off", log_dir=tmp_path / "telemetry"))

    with telemetry.span("amb.test", {"namespace": "project:test"}):
        pass

    assert _read_spans(tmp_path / "telemetry") == []


def test_jsonl_telemetry_keeps_context_and_drops_unsafe_fields(tmp_path: Path) -> None:
    log_dir = tmp_path / "telemetry"
    telemetry = Telemetry(TelemetryConfig(mode="jsonl", log_dir=log_dir, service_name="amb-test"))

    with telemetry.span("amb.root", {"namespace": "project:test", "query": "never write this"}) as root:
        root.set_attribute("result_count", 1)
        with telemetry.span("amb.child", {"content": "sensitive", "reason": "ok"}) as child:
            child.set_attribute("status", "ok")

    spans = _read_spans(log_dir)

    assert len(spans) == 2
    root_span = next(entry for entry in spans if entry["name"] == "amb.root")
    child_span = next(entry for entry in spans if entry["name"] == "amb.child")
    assert root_span["service"] == "amb-test"
    assert root_span["name"] == "amb.root"
    assert root_span["attributes"]["namespace"] == "project:test"
    assert "query" not in root_span["attributes"]
    assert child_span["trace_id"] == root_span["trace_id"]
    assert child_span["parent_span_id"] == root_span["span_id"]
    assert "content" not in child_span["attributes"]
    assert child_span["attributes"]["reason"] == "ok"


def test_memory_store_emits_metadata_only_spans(tmp_path: Path) -> None:
    telemetry = Telemetry(TelemetryConfig(mode="jsonl", log_dir=tmp_path / "telemetry"))
    store = MemoryStore(tmp_path / "memory.db", log_dir=tmp_path / "logs", telemetry=telemetry)

    store.store(
        namespace="project:test",
        content="claim: Use WAL mode.",
        kind="memory",
        tags=["topic:storage", "kind:learn"],
        actor="cole",
    )
    store.recall(namespace="project:test", query="WAL mode", limit=5)
    store.store(namespace="project:test", content="review ready", kind="signal", tags=["handoff:review"])
    signal = store.claim_signal(namespace="project:test", consumer="reviewer-a", lease_seconds=60)
    store.extend_signal_lease(signal["item"]["id"], consumer="reviewer-a", lease_seconds=60)
    store.ack_signal(signal["item"]["id"], consumer="reviewer-a")

    spans = _read_spans(tmp_path / "telemetry")
    names = [entry["name"] for entry in spans]

    assert "amb.store.write" in names
    assert "amb.store.recall" in names
    assert "amb.signal.claim" in names
    assert "amb.signal.extend" in names
    assert "amb.signal.ack" in names

    recall_span = next(entry for entry in spans if entry["name"] == "amb.store.recall")
    assert recall_span["attributes"]["query_present"] is True
    assert recall_span["attributes"]["query_length"] == len("WAL mode")
    assert "query" not in recall_span["attributes"]

    claim_span = next(entry for entry in spans if entry["name"] == "amb.signal.claim")
    assert claim_span["attributes"]["consumer_hash"]
    assert claim_span["attributes"]["claimed"] is True
