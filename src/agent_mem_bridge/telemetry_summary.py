from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def load_telemetry_spans(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    items: list[dict[str, Any]] = []
    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            items.append(parsed)
    return items


def summarize_telemetry(
    spans: list[dict[str, Any]],
    *,
    log_path: Path | None = None,
    hours: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    reference_now = now.astimezone(UTC) if now is not None else datetime.now(UTC)
    filtered = _filter_spans(spans, hours=hours, now=reference_now)
    timestamps = [_parse_ts(span.get("ts")) for span in filtered if _parse_ts(span.get("ts")) is not None]
    names = Counter(str(span.get("name") or "") for span in filtered if span.get("name"))
    namespaces = Counter(
        str(attributes.get("namespace"))
        for span in filtered
        for attributes in [_attributes(span)]
        if attributes.get("namespace")
    )

    latency_rows = []
    grouped_durations: dict[str, list[float]] = defaultdict(list)
    for span in filtered:
        name = str(span.get("name") or "")
        duration = _as_float(_attributes(span).get("duration_ms"))
        if name and duration is not None:
            grouped_durations[name].append(duration)
    for name, durations in sorted(grouped_durations.items(), key=lambda item: (-len(item[1]), item[0])):
        latency_rows.append(
            {
                "name": name,
                "count": len(durations),
                "avg_duration_ms": _round(sum(durations) / len(durations)),
                "p50_duration_ms": _percentile(durations, 50),
                "p95_duration_ms": _percentile(durations, 95),
                "max_duration_ms": _round(max(durations)),
            }
        )

    recall_spans = [span for span in filtered if span.get("name") == "amb.store.recall"]
    signal_claim_spans = [span for span in filtered if span.get("name") == "amb.signal.claim"]
    signal_extend_spans = [span for span in filtered if span.get("name") == "amb.signal.extend"]
    signal_ack_spans = [span for span in filtered if span.get("name") == "amb.signal.ack"]
    service_poll_spans = [span for span in filtered if span.get("name") == "amb.service.poll_cycle"]
    service_run_once_spans = [span for span in filtered if span.get("name") == "amb.service.run_once"]
    memory_write_spans = [
        span
        for span in filtered
        if span.get("name") == "amb.store.write" and _attributes(span).get("kind") == "memory"
    ]
    signal_write_spans = [
        span
        for span in filtered
        if span.get("name") == "amb.store.write" and _attributes(span).get("kind") == "signal"
    ]

    summary = {
        "generated_at": reference_now.isoformat(),
        "log_path": str(log_path) if log_path is not None else None,
        "time_window_hours": hours,
        "span_count": len(filtered),
        "oldest_ts": min(timestamps).isoformat() if timestamps else None,
        "newest_ts": max(timestamps).isoformat() if timestamps else None,
        "span_counts": [
            {"name": name, "count": count}
            for name, count in sorted(names.items(), key=lambda item: (-item[1], item[0]))
        ],
        "top_namespaces": [
            {"namespace": namespace, "count": count}
            for namespace, count in sorted(namespaces.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        "latency": latency_rows,
        "recall": _summarize_recall(recall_spans),
        "signals": {
            "signal_write_count": len(signal_write_spans),
            "memory_write_count": len(memory_write_spans),
            "claim": _summarize_boolean_outcome(signal_claim_spans, success_key="claimed"),
            "extend": _summarize_boolean_outcome(signal_extend_spans, success_key="extended"),
            "ack": _summarize_boolean_outcome(signal_ack_spans, success_key="acked"),
        },
        "service": {
            "poll_cycle_count": len(service_poll_spans),
            "run_once_count": len(service_run_once_spans),
            "watcher_processed_total": _sum_attribute(filtered, "watcher_processed_count"),
            "reflex_processed_total": _sum_attribute(filtered, "reflex_processed_count"),
            "consolidation_processed_total": _sum_attribute(filtered, "consolidation_processed_count"),
        },
    }
    return summary


def render_telemetry_summary_text(summary: dict[str, Any]) -> str:
    lines = [
        "Telemetry Summary",
        f"span_count: {summary['span_count']}",
        f"time_window_hours: {summary['time_window_hours'] if summary['time_window_hours'] is not None else 'all'}",
        f"oldest_ts: {summary['oldest_ts'] or 'n/a'}",
        f"newest_ts: {summary['newest_ts'] or 'n/a'}",
        "",
        "Span Counts",
    ]
    for row in summary["span_counts"][:10]:
        lines.append(f"- {row['name']}: {row['count']}")

    lines.extend(["", "Top Namespaces"])
    if summary["top_namespaces"]:
        for row in summary["top_namespaces"]:
            lines.append(f"- {row['namespace']}: {row['count']}")
    else:
        lines.append("- none")

    recall = summary["recall"]
    lines.extend(
        [
            "",
            "Recall",
            f"- call_count: {recall['call_count']}",
            f"- zero_result_count: {recall['zero_result_count']}",
            f"- avg_result_count: {recall['avg_result_count']}",
            f"- p95_duration_ms: {recall['p95_duration_ms']}",
        ]
    )

    signals = summary["signals"]
    lines.extend(
        [
            "",
            "Signals",
            f"- signal_write_count: {signals['signal_write_count']}",
            f"- claim_success_count: {signals['claim']['success_count']}",
            f"- claim_failure_count: {signals['claim']['failure_count']}",
            f"- extend_success_count: {signals['extend']['success_count']}",
            f"- extend_failure_count: {signals['extend']['failure_count']}",
            f"- ack_success_count: {signals['ack']['success_count']}",
            f"- ack_failure_count: {signals['ack']['failure_count']}",
        ]
    )

    service = summary["service"]
    lines.extend(
        [
            "",
            "Service",
            f"- poll_cycle_count: {service['poll_cycle_count']}",
            f"- run_once_count: {service['run_once_count']}",
            f"- watcher_processed_total: {service['watcher_processed_total']}",
            f"- reflex_processed_total: {service['reflex_processed_total']}",
            f"- consolidation_processed_total: {service['consolidation_processed_total']}",
        ]
    )

    lines.extend(["", "Latency"])
    if summary["latency"]:
        for row in summary["latency"][:10]:
            lines.append(
                f"- {row['name']}: count={row['count']} avg_ms={row['avg_duration_ms']} "
                f"p50_ms={row['p50_duration_ms']} p95_ms={row['p95_duration_ms']} max_ms={row['max_duration_ms']}"
            )
    else:
        lines.append("- none")
    return "\n".join(lines)


def _filter_spans(spans: list[dict[str, Any]], *, hours: float | None, now: datetime) -> list[dict[str, Any]]:
    if hours is None:
        return list(spans)
    threshold = now - timedelta(hours=hours)
    filtered = []
    for span in spans:
        ts = _parse_ts(span.get("ts"))
        if ts is not None and ts >= threshold:
            filtered.append(span)
    return filtered


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        return None


def _attributes(span: dict[str, Any]) -> dict[str, Any]:
    attributes = span.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _as_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _round(value: float) -> float:
    return round(value, 3)


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return _round(ordered[0])
    rank = (len(ordered) - 1) * (percentile / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return _round(ordered[lower])
    fraction = rank - lower
    return _round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _summarize_recall(spans: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [_as_float(_attributes(span).get("duration_ms")) for span in spans]
    durations = [duration for duration in durations if duration is not None]
    result_counts = [_attributes(span).get("result_count") for span in spans]
    numeric_counts = [int(count) for count in result_counts if isinstance(count, int)]
    zero_result_count = sum(1 for count in numeric_counts if count == 0)
    return {
        "call_count": len(spans),
        "zero_result_count": zero_result_count,
        "avg_result_count": _round(sum(numeric_counts) / len(numeric_counts)) if numeric_counts else 0.0,
        "avg_duration_ms": _round(sum(durations) / len(durations)) if durations else 0.0,
        "p95_duration_ms": _percentile(durations, 95),
    }


def _summarize_boolean_outcome(spans: list[dict[str, Any]], *, success_key: str) -> dict[str, Any]:
    reason_counts = Counter()
    success_count = 0
    failure_count = 0
    for span in spans:
        attributes = _attributes(span)
        if attributes.get(success_key) is True:
            success_count += 1
        else:
            failure_count += 1
            reason = attributes.get("reason")
            if isinstance(reason, str) and reason.strip():
                reason_counts[reason.strip()] += 1
    return {
        "call_count": len(spans),
        "success_count": success_count,
        "failure_count": failure_count,
        "failure_reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def _sum_attribute(spans: list[dict[str, Any]], key: str) -> int:
    total = 0
    for span in spans:
        value = _attributes(span).get(key)
        if isinstance(value, int):
            total += value
    return total
