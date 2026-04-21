from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ControlLevel = Literal["signal", "reflection", "belief", "policy"]
ProfileRecordType = Literal["persona", "soul", "core-policy"]


CONTROL_LEVELS: tuple[ControlLevel, ...] = ("signal", "reflection", "belief", "policy")
PROFILE_RECORD_TYPES: tuple[ProfileRecordType, ...] = ("persona", "soul", "core-policy")


@dataclass(frozen=True, slots=True)
class StartupRecallLayer:
    label: str
    namespace: str
    tags_any: tuple[str, ...] = ()
    required: bool = True
    reason: str = ""


def canonical_profile_tags(
    *,
    record_type: ProfileRecordType,
    control_level: ControlLevel = "policy",
    domains: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if record_type not in PROFILE_RECORD_TYPES:
        raise ValueError(f"Unsupported profile record type: {record_type}")
    if control_level not in CONTROL_LEVELS:
        raise ValueError(f"Unsupported control level: {control_level}")

    tags = [f"record:{record_type}", f"control:{control_level}"]
    for domain in domains:
        cleaned = domain.strip()
        if cleaned:
            tags.append(cleaned)
    return tuple(_unique(tags))


def build_startup_recall_plan(
    *,
    global_namespace: str,
    project_namespace: str | None = None,
    specialization_namespaces: tuple[str, ...] = (),
    issue_mode: bool = False,
) -> tuple[StartupRecallLayer, ...]:
    layers: list[StartupRecallLayer] = [
        StartupRecallLayer(
            label="core-policy",
            namespace=global_namespace,
            tags_any=("record:core-policy",),
            reason="Load durable operating rules first.",
        ),
        StartupRecallLayer(
            label="persona",
            namespace=global_namespace,
            tags_any=("record:persona",),
            reason="Load communication and collaboration style.",
        ),
        StartupRecallLayer(
            label="soul",
            namespace=global_namespace,
            tags_any=("record:soul",),
            reason="Load longer-arc identity and continuity.",
        ),
    ]

    for specialization in specialization_namespaces:
        cleaned = specialization.strip()
        if not cleaned:
            continue
        layers.append(
            StartupRecallLayer(
                label=f"specialization:{cleaned}",
                namespace=cleaned,
                required=False,
                reason="Optional specialization overlay for the current task.",
            )
        )

    if project_namespace:
        layers.append(
            StartupRecallLayer(
                label="project",
                namespace=project_namespace.strip(),
                reason="Project-local execution memory belongs in the default startup stack.",
            )
        )

    if issue_mode:
        if project_namespace:
            layers.append(
                StartupRecallLayer(
                    label="project-gotchas",
                    namespace=project_namespace.strip(),
                    tags_any=("kind:gotcha",),
                    required=False,
                    reason="Issue-like work should check project gotchas before browsing externally.",
                )
            )
        layers.append(
            StartupRecallLayer(
                label="global-gotchas",
                namespace=global_namespace,
                tags_any=("kind:gotcha",),
                required=False,
                reason="Issue-like work should check reusable gotchas before browsing externally.",
            )
        )
        layers.append(
            StartupRecallLayer(
                label="domain-notes",
                namespace=global_namespace,
                tags_any=("kind:domain-note",),
                required=False,
                reason="Domain notes help combine project and global judgment.",
            )
        )

    return tuple(layers)


def render_startup_recall_plan(plan: tuple[StartupRecallLayer, ...]) -> str:
    lines: list[str] = []
    for index, layer in enumerate(plan, start=1):
        lines.append(f"{index}. {layer.label}")
        lines.append(f"   namespace: {layer.namespace}")
        if layer.tags_any:
            lines.append(f"   tags_any: {', '.join(layer.tags_any)}")
        lines.append(f"   required: {'yes' if layer.required else 'no'}")
        if layer.reason:
            lines.append(f"   reason: {layer.reason}")
    return "\n".join(lines)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique
