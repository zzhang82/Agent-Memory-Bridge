from pathlib import Path

from agent_mem_bridge.profile_bundle import load_profile_bundle, render_profile_bundle, startup_records


def test_load_profile_bundle_and_render(tmp_path: Path) -> None:
    bundle_path = tmp_path / "bundle.toml"
    bundle_path.write_text(
        """
[bundle]
name = "Test Bundle"
namespace = "shadow:test"

[[record]]
title = "Core Safety"
record_type = "core-policy"
control_level = "policy"
startup_load = true
tags = ["kind:policy"]
domains = ["domain:reliability"]
source_refs = ["REDLINE.md"]
content = "Prefer reversible actions."

[[record]]
title = "Voice"
record_type = "persona"
control_level = "policy"
startup_load = true
content = "Be calm and direct."
""",
        encoding="utf-8",
    )

    bundle = load_profile_bundle(bundle_path)

    assert bundle.name == "Test Bundle"
    assert bundle.namespace == "shadow:test"
    assert len(bundle.records) == 2
    assert startup_records(bundle)[0].title == "Core Safety"

    rendered = render_profile_bundle(bundle)
    assert "name: Test Bundle" in rendered
    assert "startup_records:" in rendered
    assert "Core Safety" in rendered
