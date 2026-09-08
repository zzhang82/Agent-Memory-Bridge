"""Evaluate frozen semantic memory-invocation evidence for v0.33.1."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from agent_mem_bridge.invocation_evidence import InvocationEvidenceError, load_trial_document  # noqa: E402
from agent_mem_bridge.mcp_boundary import package_version  # noqa: E402
from agent_mem_bridge.semantic_eval import evaluate_semantic_invocation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate frozen AMB semantic memory-invocation evidence.")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "benchmark" / "semantic-invocation-v1.json",
        help="Frozen evaluation fixture.",
    )
    parser.add_argument(
        "--rubric",
        type=Path,
        default=ROOT / "benchmark" / "semantic-invocation-rubric-v1.md",
        help="Frozen rubric whose companion SHA-256 file must match.",
    )
    parser.add_argument(
        "--rubric-hash",
        type=Path,
        default=None,
        help="Optional rubric SHA-256 file. Defaults to a sibling <rubric-stem>.sha256 file.",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        action="append",
        default=[],
        help="Sanitized host trial document. Repeat for multiple hosts.",
    )
    parser.add_argument(
        "--source-version",
        default=None,
        help="Source version asserted by the evidence run. Defaults to package metadata.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    args = parser.parse_args()

    try:
        rubric_sha256, rubric_hash_path = _verify_rubric(args.rubric, args.rubric_hash)
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        if not isinstance(fixture, dict):
            raise ValueError("semantic invocation fixture must be a JSON object")
        records = []
        for evidence_path in args.evidence:
            records.extend(load_trial_document(evidence_path))
        source_version = args.source_version or package_version()
        report = evaluate_semantic_invocation(fixture, records, source_version=source_version)
        report["rubric"] = {
            "path": _portable_report_path(args.rubric),
            "sha256": rubric_sha256,
            "hash_path": _portable_report_path(rubric_hash_path),
        }
    except (OSError, json.JSONDecodeError, InvocationEvidenceError, ValueError) as exc:
        print(f"semantic invocation evaluation failed: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if not report["validation_errors"] else 1


def _verify_rubric(path: Path, hash_path: Path | None) -> tuple[str, Path]:
    rubric_bytes = path.read_bytes()
    actual = hashlib.sha256(rubric_bytes).hexdigest()
    expected_path = hash_path or path.with_name(f"{path.stem}.sha256")
    expected_text = expected_path.read_text(encoding="utf-8").strip()
    expected = expected_text.split()[0] if expected_text else ""
    if expected != actual:
        raise ValueError(f"rubric SHA-256 mismatch for {path}: expected {expected or '<missing>'}, got {actual}")
    return actual, expected_path


def _portable_report_path(path: Path) -> str:
    """Keep checked-in reports independent of the checkout's absolute path."""

    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


if __name__ == "__main__":
    raise SystemExit(main())
