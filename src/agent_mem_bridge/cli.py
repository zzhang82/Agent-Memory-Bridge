from __future__ import annotations

import argparse
import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Sequence

from .client_config import build_client_config_options, render_client_config, supported_client_names
from .index_health import inspect_indexes, rebuild_embedding_index, rebuild_fts_index
from .onboarding import render_report, render_verify_success_message, run_doctor, run_verify
from .paths import resolve_bridge_home, resolve_config_path
from .server import main as serve_server
from .storage import MemoryStore


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        serve_server()
        return 0
    if args[0] in {"-V", "--version"}:
        print(_package_version())
        return 0

    parser = _build_parser()
    namespace = parser.parse_args(args)

    if namespace.command == "serve":
        serve_server()
        return 0
    if namespace.command == "config":
        return _run_config(namespace)
    if namespace.command == "doctor":
        return _run_doctor(namespace)
    if namespace.command == "verify":
        return _run_verify(namespace)
    if namespace.command == "index-health":
        return _run_index_health(namespace)
    if namespace.command == "index-rebuild":
        return _run_index_rebuild(namespace)

    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Memory Bridge CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Start the MCP stdio server.")

    config_parser = subparsers.add_parser("config", help="Render a client config fragment.")
    config_parser.add_argument("--client", required=True, choices=supported_client_names())
    config_parser.add_argument(
        "--python",
        dest="python_path",
        default=sys.executable,
        help="Python executable that should launch `-m agent_mem_bridge`.",
    )
    config_parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Working directory to embed in the client config.",
    )
    config_parser.add_argument(
        "--bridge-home",
        type=Path,
        default=resolve_bridge_home(),
        help="Bridge home path to embed in the client config.",
    )
    config_parser.add_argument(
        "--config-path",
        type=Path,
        default=resolve_config_path(),
        help="Config path to embed in the client config.",
    )
    config_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path. Defaults to stdout.",
    )
    config_parser.add_argument("--force", action="store_true", help="Allow overwriting --output.")
    config_parser.add_argument(
        "--example",
        action="store_true",
        help="Render placeholder-safe example output instead of local runtime paths.",
    )

    doctor_parser = subparsers.add_parser("doctor", help="Run non-invasive onboarding checks.")
    doctor_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")
    doctor_parser.add_argument(
        "--include-stdio",
        action="store_true",
        help="Also run an isolated stdio verify check.",
    )
    doctor_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Project root used for the optional stdio verify check.",
    )

    verify_parser = subparsers.add_parser("verify", help="Run an isolated stdio smoke test.")
    verify_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")
    verify_parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Project root used to launch `python -m agent_mem_bridge`.",
    )
    verify_parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=None,
        help="Optional runtime directory. Defaults to an isolated temporary directory.",
    )

    index_health_parser = subparsers.add_parser("index-health", help="Inspect derived FTS and embedding index health.")
    index_health_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")
    index_health_parser.add_argument(
        "--strict-embeddings",
        action="store_true",
        help="Also require the optional semantic sidecar to be fully populated.",
    )

    index_rebuild_parser = subparsers.add_parser("index-rebuild", help="Rebuild derived indexes without changing memory rows.")
    index_rebuild_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")
    index_rebuild_parser.add_argument("--fts", action="store_true", help="Rebuild the FTS5 derived index.")
    index_rebuild_parser.add_argument("--embeddings", action="store_true", help="Rebuild the local semantic sidecar index.")
    return parser


def _run_config(namespace: argparse.Namespace) -> int:
    options = build_client_config_options(
        namespace.client,
        python_path=namespace.python_path,
        cwd=namespace.cwd,
        bridge_home=namespace.bridge_home,
        config_path=namespace.config_path,
        example=namespace.example,
    )
    rendered = render_client_config(options)

    if namespace.output is not None:
        output_path: Path = namespace.output
        if output_path.exists() and not namespace.force:
            print(f"Refusing to overwrite existing file: {output_path}", file=sys.stderr)
            return 3
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered.content + "\n", encoding="utf-8")
        print(str(output_path))
        return 0

    print(rendered.content)
    return 0


def _run_doctor(namespace: argparse.Namespace) -> int:
    report = run_doctor(include_stdio=namespace.include_stdio, project_root=namespace.project_root)
    if namespace.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_report(report))
    return 0 if report["ok"] else 1


def _run_verify(namespace: argparse.Namespace) -> int:
    report = run_verify(project_root=namespace.project_root, runtime_dir=namespace.runtime_dir)
    if namespace.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_verify_success_message(report))
        print(render_report(report))
    return 0 if report["ok"] else 1


def _run_index_health(namespace: argparse.Namespace) -> int:
    store = MemoryStore.from_env()
    with store._connect() as conn:
        report = inspect_indexes(conn)
    if namespace.json:
        print(json.dumps(report, indent=2))
    else:
        print(_render_index_health(report))
    healthy = _index_health_ok(report, strict_embeddings=namespace.strict_embeddings)
    return 0 if healthy else 1


def _run_index_rebuild(namespace: argparse.Namespace) -> int:
    rebuild_fts = bool(namespace.fts)
    rebuild_embeddings = bool(namespace.embeddings)
    if not rebuild_fts and not rebuild_embeddings:
        rebuild_fts = True
        rebuild_embeddings = True
    store = MemoryStore.from_env()
    with store._connect() as conn:
        if rebuild_fts:
            report = rebuild_fts_index(conn)
        else:
            report = inspect_indexes(conn)
        if rebuild_embeddings:
            report = rebuild_embedding_index(conn)
        conn.commit()
    if namespace.json:
        print(json.dumps(report, indent=2))
    else:
        print(_render_index_health(report))
    healthy = bool(report["fts"]["healthy"])
    if rebuild_embeddings:
        healthy = healthy and bool(report["embeddings"]["healthy"])
    return 0 if healthy else 1


def _index_health_ok(report: dict[str, object], *, strict_embeddings: bool = False) -> bool:
    fts = report["fts"]
    embeddings = report["embeddings"]
    assert isinstance(fts, dict)
    assert isinstance(embeddings, dict)
    if not bool(fts["healthy"]):
        return False
    if strict_embeddings and not bool(embeddings["healthy"]):
        return False
    return True


def _render_index_health(report: dict[str, object]) -> str:
    fts = report["fts"]
    embeddings = report["embeddings"]
    assert isinstance(fts, dict)
    assert isinstance(embeddings, dict)
    lines = [
        "# AMB Index Health",
        "",
        f"- memory_count: `{report['memory_count']}`",
        f"- fts_index_count: `{fts['index_count']}`",
        f"- fts_missing_count: `{fts['missing_count']}`",
        f"- fts_orphan_count: `{fts['orphan_count']}`",
        f"- fts_stale_count: `{fts['stale_count']}`",
        f"- embedding_count: `{embeddings['embedding_count']}`",
        f"- missing_embedding_count: `{embeddings['missing_embedding_count']}`",
        f"- stale_embedding_count: `{embeddings['stale_embedding_count']}`",
        f"- orphan_embedding_count: `{embeddings['orphan_embedding_count']}`",
        "",
        "Derived indexes can be rebuilt without changing `memories` rows.",
    ]
    return "\n".join(lines)


def _package_version() -> str:
    try:
        return version("agent-memory-bridge")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if pyproject_path.exists():
            for line in pyproject_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("version = "):
                    return line.split("=", 1)[1].strip().strip('"')
        return "0.0.0"
