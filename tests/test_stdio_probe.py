from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_mem_bridge import stdio_probe


def test_probe_error_report_redacts_exception_details(tmp_path: Path, monkeypatch) -> None:
    private_path = "/home/private-user/secret-project"
    raw_token = "token-that-must-not-leak"
    raw_query = "private customer query"

    async def fail_probe(*_: object, **__: object) -> dict[str, object]:
        raise RuntimeError(f"{private_path} {raw_token} {raw_query}")

    monkeypatch.setattr(stdio_probe, "_run_era_probe", fail_probe)

    report = asyncio.run(
        stdio_probe._run_era_safely(
            tmp_path,
            tmp_path / "runtime",
            mode="auto",
        )
    )
    encoded = json.dumps(report, sort_keys=True)

    assert report == {
        "ok": False,
        "mode": "modern",
        "error_type": "RuntimeError",
        "error": "isolated stdio protocol probe failed",
    }
    assert private_path not in encoded
    assert raw_token not in encoded
    assert raw_query not in encoded
