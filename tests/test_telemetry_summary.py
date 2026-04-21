import json
from datetime import UTC, datetime
from pathlib import Path

from agent_mem_bridge.telemetry_summary import (
    load_telemetry_spans,
    render_telemetry_summary_text,
    summarize_telemetry,
)


def test_load_telemetry_spans_reads_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "spans.jsonl"
    log_path.write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-04-12T22:00:00+00:00", "name": "amb.test", "attributes": {"duration_ms": 5.0}}),
                "",
                json.dumps({"ts": "2026-04-12T22:01:00+00:00", "name": "amb.test2", "attributes": {"duration_ms": 7.0}}),
            ]
        ),
        encoding="utf-8",
    )

    spans = load_telemetry_spans(log_path)

    assert len(spans) == 2
    assert spans[0]["name"] == "amb.test"
    assert spans[1]["name"] == "amb.test2"


def test_summarize_telemetry_aggregates_core_metrics() -> None:
    spans = [
        {
            "ts": "2026-04-12T22:00:00+00:00",
            "name": "amb.store.recall",
            "attributes": {"namespace": "project:alpha", "duration_ms": 10.0, "result_count": 1},
        },
        {
            "ts": "2026-04-12T22:10:00+00:00",
            "name": "amb.store.recall",
            "attributes": {"namespace": "project:alpha", "duration_ms": 30.0, "result_count": 0},
        },
        {
            "ts": "2026-04-12T22:15:00+00:00",
            "name": "amb.signal.claim",
            "attributes": {"namespace": "project:alpha", "duration_ms": 5.0, "claimed": False, "reason": "none-eligible"},
        },
        {
            "ts": "2026-04-12T22:20:00+00:00",
            "name": "amb.signal.claim",
            "attributes": {"namespace": "project:alpha", "duration_ms": 6.0, "claimed": True},
        },
        {
            "ts": "2026-04-12T22:25:00+00:00",
            "name": "amb.signal.extend",
            "attributes": {"namespace": "project:alpha", "duration_ms": 4.0, "extended": True},
        },
        {
            "ts": "2026-04-12T22:30:00+00:00",
            "name": "amb.signal.ack",
            "attributes": {"namespace": "project:alpha", "duration_ms": 4.5, "acked": True},
        },
        {
            "ts": "2026-04-12T22:40:00+00:00",
            "name": "amb.service.poll_cycle",
            "attributes": {
                "duration_ms": 100.0,
                "watcher_processed_count": 2,
                "reflex_processed_count": 1,
                "consolidation_processed_count": 0,
            },
        },
        {
            "ts": "2026-04-12T22:45:00+00:00",
            "name": "amb.store.write",
            "attributes": {"namespace": "project:alpha", "duration_ms": 3.0, "kind": "signal"},
        },
        {
            "ts": "2026-04-12T22:50:00+00:00",
            "name": "amb.store.write",
            "attributes": {"namespace": "project:beta", "duration_ms": 2.0, "kind": "memory"},
        },
    ]

    summary = summarize_telemetry(
        spans,
        hours=2,
        now=datetime(2026, 4, 12, 23, 0, tzinfo=UTC),
    )

    assert summary["span_count"] == 9
    assert summary["recall"]["call_count"] == 2
    assert summary["recall"]["zero_result_count"] == 1
    assert summary["recall"]["avg_result_count"] == 0.5
    assert summary["signals"]["claim"]["success_count"] == 1
    assert summary["signals"]["claim"]["failure_count"] == 1
    assert summary["signals"]["claim"]["failure_reasons"][0]["reason"] == "none-eligible"
    assert summary["signals"]["signal_write_count"] == 1
    assert summary["signals"]["memory_write_count"] == 1
    assert summary["service"]["poll_cycle_count"] == 1
    assert summary["service"]["watcher_processed_total"] == 2
    assert summary["top_namespaces"][0]["namespace"] == "project:alpha"
    recall_latency = next(row for row in summary["latency"] if row["name"] == "amb.store.recall")
    assert recall_latency["count"] == 2
    assert recall_latency["p95_duration_ms"] == 29.0


def test_render_telemetry_summary_text_contains_core_sections() -> None:
    summary = {
        "span_count": 3,
        "time_window_hours": 24,
        "oldest_ts": "2026-04-12T22:00:00+00:00",
        "newest_ts": "2026-04-12T23:00:00+00:00",
        "span_counts": [{"name": "amb.store.recall", "count": 2}],
        "top_namespaces": [{"namespace": "project:alpha", "count": 2}],
        "recall": {"call_count": 2, "zero_result_count": 1, "avg_result_count": 0.5, "p95_duration_ms": 30.0},
        "signals": {
            "signal_write_count": 1,
            "claim": {"success_count": 1, "failure_count": 0},
            "extend": {"success_count": 1, "failure_count": 0},
            "ack": {"success_count": 1, "failure_count": 0},
        },
        "service": {
            "poll_cycle_count": 1,
            "run_once_count": 0,
            "watcher_processed_total": 1,
            "reflex_processed_total": 0,
            "consolidation_processed_total": 0,
        },
        "latency": [
            {
                "name": "amb.store.recall",
                "count": 2,
                "avg_duration_ms": 20.0,
                "p50_duration_ms": 20.0,
                "p95_duration_ms": 30.0,
                "max_duration_ms": 30.0,
            }
        ],
    }

    rendered = render_telemetry_summary_text(summary)

    assert "Telemetry Summary" in rendered
    assert "Span Counts" in rendered
    assert "Top Namespaces" in rendered
    assert "Recall" in rendered
    assert "Signals" in rendered
    assert "Service" in rendered
    assert "Latency" in rendered
