import pytest

from agent_mem_bridge.profile_assembly import (
    build_startup_recall_plan,
    canonical_profile_tags,
    render_startup_recall_plan,
)


def test_canonical_profile_tags_are_generic_and_weighted() -> None:
    tags = canonical_profile_tags(
        record_type="core-policy",
        control_level="policy",
        domains=("domain:agent-memory", "domain:reliability"),
    )

    assert tags == (
        "record:core-policy",
        "control:policy",
        "domain:agent-memory",
        "domain:reliability",
    )


def test_canonical_profile_tags_reject_unknown_values() -> None:
    with pytest.raises(ValueError):
        canonical_profile_tags(record_type="voice", control_level="policy")

    with pytest.raises(ValueError):
        canonical_profile_tags(record_type="persona", control_level="durable")


def test_startup_plan_loads_profile_bundle_before_project() -> None:
    plan = build_startup_recall_plan(
        global_namespace="global",
        project_namespace="project:mem-store",
        specialization_namespaces=("cole-workflows",),
        issue_mode=False,
    )

    labels = [layer.label for layer in plan]

    assert labels[:3] == ["core-policy", "persona", "soul"]
    assert "specialization:cole-workflows" in labels
    assert labels[-1] == "project"


def test_issue_mode_adds_gotchas_and_domain_notes() -> None:
    plan = build_startup_recall_plan(
        global_namespace="global",
        project_namespace="project:mem-store",
        issue_mode=True,
    )

    labels = [layer.label for layer in plan]

    assert labels[-3:] == ["project-gotchas", "global-gotchas", "domain-notes"]
    assert plan[-3].tags_any == ("kind:gotcha",)
    assert plan[-2].tags_any == ("kind:gotcha",)
    assert plan[-1].tags_any == ("kind:domain-note",)


def test_render_startup_plan_is_readable() -> None:
    plan = build_startup_recall_plan(
        global_namespace="global",
        project_namespace="project:mem-store",
        issue_mode=True,
    )

    rendered = render_startup_recall_plan(plan)

    assert "1. core-policy" in rendered
    assert "namespace: global" in rendered
    assert "project:mem-store" in rendered
    assert "global-gotchas" in rendered
