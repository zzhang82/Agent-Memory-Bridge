from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
expected = {
    ROOT / "Dockerfile": f"ARG AMB_VERSION={version}",
    ROOT / "compose.yml": f"ghcr.io/zzhang82/agent-memory-bridge:{version}",
}
failures: list[str] = []
for path, needle in expected.items():
    text = path.read_text(encoding="utf-8")
    if needle not in text:
        failures.append(f"{path.relative_to(ROOT)} does not contain {needle}")
workflow_versions: set[str] = set()
release_pin = re.compile(r"(?:--expected-version|AMB_VERSION[:=]|agent-memory-bridge[:=])\s*[\"']?(\d+\.\d+\.\d+)")
for path in (ROOT / ".github" / "workflows").glob("*.yml"):
    workflow_versions.update(release_pin.findall(path.read_text(encoding="utf-8")))
if workflow_versions:
    failures.append(f"workflow files hardcode versions {sorted(workflow_versions)}; read pyproject.toml instead")
if failures:
    raise SystemExit("\n".join(failures))
print(f"release version pins match {version}")
