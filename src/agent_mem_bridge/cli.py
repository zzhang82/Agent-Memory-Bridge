from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Sequence

from .client_config import build_client_config_options, render_client_config, supported_client_names
from .cross_client_activation import build_activation_receipt_from_db, render_activation_receipt_markdown
from .database_maintenance import (
    backup_database,
    checkpoint_database,
    cleanup_signals,
    inspect_database,
    rebuild_database_projections,
    restore_database,
    verify_backup,
)
from .evidence_inspect import build_memory_inspect_report, render_memory_inspect_markdown
from .first_run import build_first_run_report, render_first_run_markdown
from .index_health import inspect_indexes, rebuild_embedding_index, rebuild_fts_index
from .knowledge_explorer import (
    _build_explorer,
    render_explorer_human_markdown,
    render_explorer_technical_markdown,
)
from .onboarding import render_report, render_verify_success_message, run_doctor, run_verify
from .paths import (
    resolve_bridge_db_path,
    resolve_bridge_home,
    resolve_bridge_log_dir,
    resolve_config_path,
    resolve_repository_snapshot_root,
)
from .repository_bootstrap import compile_repository_snapshot, render_snapshot_markdown
from .repository_snapshot_store import RepositorySnapshotStore
from .review_queue import build_review_queue_report, render_review_queue_markdown
from .review_workflow import build_review_workflow_report, render_review_workflow_markdown
from .run_consolidation import (
    build_run_consolidation_report,
    render_run_consolidation_markdown,
    stage_run_consolidation_report,
)
from .service_lock import ServiceFileLock, ServiceLockConflict
from .setup_apply import (
    apply_setup_plan,
    capture_setup_apply_snapshot,
    render_setup_apply_confirmation,
    render_setup_apply_result,
    render_setup_rollback_result,
    rollback_setup_plan,
)
from .setup_planner import build_setup_plan, render_setup_plan
from .storage import MemoryStore
from .task_brief import build_task_brief_report, render_task_brief_markdown


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        from .server import main as serve_server

        serve_server()
        return 0
    if args[0] in {"-V", "--version"}:
        print(_package_version())
        return 0

    parser = _build_parser()
    namespace = parser.parse_args(args)

    if namespace.command == "serve":
        from .server import main as serve_server

        serve_server()
        return 0
    if namespace.command == "service":
        return _run_service(namespace)
    if namespace.command == "config":
        return _run_config(namespace)
    if namespace.command == "setup":
        return _run_setup(namespace)
    if namespace.command == "first-run":
        return _run_first_run(namespace)
    if namespace.command == "bootstrap-repo":
        return _run_bootstrap_repo(namespace)
    if namespace.command == "unbind-repo":
        return _run_unbind_repo(namespace)
    if namespace.command == "inspect":
        return _run_inspect(namespace)
    if namespace.command == "explore":
        return _run_explore(namespace)
    if namespace.command == "doctor":
        return _run_doctor(namespace)
    if namespace.command == "verify":
        return _run_verify(namespace)
    if namespace.command == "index-health":
        return _run_index_health(namespace)
    if namespace.command == "index-rebuild":
        return _run_index_rebuild(namespace)
    if namespace.command == "review-queue":
        return _run_review_queue(namespace)
    if namespace.command == "review-workflow":
        return _run_review_workflow(namespace)
    if namespace.command == "task-brief":
        return _run_task_brief(namespace)
    if namespace.command == "activation-receipt":
        return _run_activation_receipt(namespace)
    if namespace.command == "mint-verification-receipt":
        return _run_mint_verification_receipt(namespace)
    if namespace.command == "signal-repair":
        return _run_signal_repair(namespace)
    if namespace.command == "db-health":
        return _run_db_health(namespace)
    if namespace.command == "backup":
        return _run_backup(namespace)
    if namespace.command == "verify-backup":
        return _run_verify_backup(namespace)
    if namespace.command == "restore":
        return _run_restore(namespace)
    if namespace.command == "wal-checkpoint":
        return _run_wal_checkpoint(namespace)
    if namespace.command == "signal-cleanup":
        return _run_signal_cleanup(namespace)
    if namespace.command == "consolidate-runs":
        return _run_consolidate_runs(namespace)

    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Memory Bridge CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Start the MCP stdio server.")

    service_parser = subparsers.add_parser("service", help="Run watcher, reflex, and consolidation service loop.")
    service_parser.add_argument("--once", action="store_true", help="Run one service cycle and exit.")
    service_parser.add_argument(
        "--allow-multiple-services",
        action="store_true",
        help="Explicitly bypass the bridge-home singleton service lock.",
    )

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

    setup_parser = subparsers.add_parser(
        "setup",
        help="Preview a read-only client setup plan; no configuration is written.",
    )
    setup_parser.add_argument(
        "--client",
        action="append",
        choices=supported_client_names(),
        default=None,
        help="Supported client to plan. Repeat for multiple clients; default plans every supported client.",
    )
    setup_parser.add_argument("--json", action="store_true", help="Emit deterministic JSON instead of plain text.")
    setup_parser.add_argument(
        "--apply",
        action="store_true",
        help="Explicitly apply only P2A-classified safe configuration changes after confirmation.",
    )
    setup_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm a JSON safe apply non-interactively; requires --apply and never bypasses safety checks.",
    )
    setup_parser.add_argument(
        "--rollback",
        action="store_true",
        help="Interactively roll back only the latest matching P2B-owned configuration change.",
    )
    setup_parser.add_argument(
        "--python",
        dest="python_path",
        default=sys.executable,
        help="Python executable shown in the proposed fragment; it is not executed.",
    )
    setup_parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Project directory used only for bounded workspace-config inspection.",
    )
    setup_parser.add_argument(
        "--bridge-home",
        type=Path,
        default=resolve_bridge_home(),
        help="Bridge home path shown in the proposed fragment; it is not created.",
    )
    setup_parser.add_argument(
        "--config-path",
        type=Path,
        default=resolve_config_path(),
        help="AMB config path shown in the proposed fragment; it is not read or created by setup.",
    )

    bootstrap_parser = subparsers.add_parser(
        "bootstrap-repo",
        help="Derive a bounded, commit-bound repository knowledge snapshot without changing memory.",
    )
    bootstrap_parser.add_argument("path", type=Path, help="Repository directory to inspect locally.")
    bootstrap_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="Output format.")
    bootstrap_parser.add_argument("--namespace", help="Explicit project namespace to bind to this repository.")
    bootstrap_parser.add_argument(
        "--rebind", action="store_true", help="Allow explicit replacement of an existing different namespace binding."
    )

    unbind_parser = subparsers.add_parser(
        "unbind-repo",
        help="Remove one explicit local repository binding without deleting snapshots or memory.",
    )
    unbind_parser.add_argument("--namespace", required=True, help="Project namespace to unbind locally.")
    unbind_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="Output format.")

    first_run_parser = subparsers.add_parser(
        "first-run",
        help="Guide a read-only durable-memory loop after setup connects AMB.",
    )
    first_run_parser.add_argument(
        "--namespace", default="project:demo", help="Project namespace for the durable-memory loop."
    )
    first_run_parser.add_argument(
        "--query",
        default="What should I check before submitting changes to this project?",
        help="Realistic task question for durable-memory recall.",
    )
    first_run_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format.",
    )
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Explain governed task-memory selections and relevant exclusions without changing memory.",
    )
    inspect_parser.add_argument("--namespace", required=True, help="Project namespace to inspect.")
    inspect_parser.add_argument("--query", required=True, help="Task question used by governed task memory.")
    inspect_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="Output format.")
    inspect_parser.add_argument(
        "--technical", action="store_true", help="Include bounded ids, reason codes, and existing selection metadata."
    )
    explore_parser = subparsers.add_parser(
        "explore",
        help="Explore a bounded read-only projection of known project relationships.",
    )
    explore_parser.add_argument("--namespace", required=True, help="Project namespace to explore.")
    explore_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="Output format.")
    explore_parser.add_argument("--limit", type=int, default=100, help="Maximum source records to project (1..500).")
    explore_parser.add_argument(
        "--technical",
        action="store_true",
        help="Render the detailed graph/audit Markdown. Valid only with --format markdown.",
    )

    # Retained parser compatibility only. P2C does not inspect or render these
    # values, so they must not appear as meaningful first-run controls.
    first_run_parser.add_argument(
        "--client", default="generic", choices=supported_client_names(), help=argparse.SUPPRESS
    )
    first_run_parser.add_argument("--python", dest="python_path", default=sys.executable, help=argparse.SUPPRESS)
    first_run_parser.add_argument("--cwd", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    first_run_parser.add_argument("--bridge-home", type=Path, default=resolve_bridge_home(), help=argparse.SUPPRESS)
    first_run_parser.add_argument("--config-path", type=Path, default=resolve_config_path(), help=argparse.SUPPRESS)
    first_run_parser.add_argument("--example", action="store_true", help=argparse.SUPPRESS)

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

    index_rebuild_parser = subparsers.add_parser(
        "index-rebuild", help="Rebuild derived indexes without changing memory rows."
    )
    index_rebuild_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")
    index_rebuild_parser.add_argument("--fts", action="store_true", help="Rebuild the FTS5 derived index.")
    index_rebuild_parser.add_argument(
        "--embeddings", action="store_true", help="Rebuild the local semantic sidecar index."
    )

    review_queue_parser = subparsers.add_parser(
        "review-queue",
        help="Render a read-only operator review queue for staged and dispositioned memory records.",
    )
    review_queue_parser.add_argument("--namespace", required=True, help="Namespace to inspect.")
    review_queue_parser.add_argument("--limit", type=int, default=100, help="Maximum rows/items to scan and return.")
    review_queue_parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Include closed review receipts in addition to open/actionable items.",
    )
    review_queue_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format.",
    )
    review_workflow_parser = subparsers.add_parser(
        "review-workflow",
        help="Render a proposal-only human workflow plan from the operator review queue.",
    )
    review_workflow_parser.add_argument("--namespace", required=True, help="Namespace to inspect.")
    review_workflow_parser.add_argument("--limit", type=int, default=100, help="Maximum rows/items to scan and return.")
    review_workflow_parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Include closed review receipts in addition to open/actionable items.",
    )
    review_workflow_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format.",
    )
    task_brief_parser = subparsers.add_parser(
        "task-brief",
        help="Render a read-only Task Brief from task memory, review queue, and active signals.",
    )
    task_brief_parser.add_argument("--namespace", required=True, help="Project namespace to inspect.")
    task_brief_parser.add_argument("--query", required=True, help="Task query used to assemble task memory.")
    task_brief_parser.add_argument(
        "--global-namespace",
        default="global",
        help="Global namespace used for supporting task memory.",
    )
    task_brief_parser.add_argument(
        "--review-limit",
        type=int,
        default=100,
        help="Maximum review-queue rows/items to scan.",
    )
    task_brief_parser.add_argument(
        "--signal-limit",
        type=int,
        default=20,
        help="Maximum active signals to include.",
    )
    task_brief_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format.",
    )
    activation_receipt_parser = subparsers.add_parser(
        "activation-receipt",
        help="Render a read-only cross-client activation receipt for a correlation id.",
    )
    activation_receipt_parser.add_argument("--namespace", required=True, help="Namespace to inspect.")
    activation_receipt_parser.add_argument("--correlation-id", required=True, help="Correlation id to inspect.")
    activation_receipt_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format.",
    )
    verification_receipt_parser = subparsers.add_parser(
        "mint-verification-receipt",
        help="Mint one bounded human/operator governed-run verification receipt.",
    )
    verification_receipt_parser.add_argument(
        "--workspace-key", required=True, help="Declared workspace that owns the run."
    )
    verification_receipt_parser.add_argument("--run-id", required=True, help="Server-minted governed run id.")
    verification_receipt_parser.add_argument(
        "--preflight-event-id", required=True, help="Approved same-run governed preflight_review event id."
    )
    verification_receipt_parser.add_argument(
        "--evaluator-digest", required=True, help="Lowercase SHA-256 digest of the human review protocol."
    )
    verification_receipt_parser.add_argument(
        "--evaluator-version", required=True, help="Bounded human review protocol version."
    )
    verification_receipt_parser.add_argument(
        "--criterion-results-json",
        required=True,
        help="JSON array with one passed/failed result and evidence_refs for every criterion.",
    )
    verification_receipt_parser.add_argument(
        "--evidence-json", required=True, help="JSON array of bounded human review evidence references."
    )
    verification_receipt_parser.add_argument(
        "--result",
        default="verified_success",
        choices=("verified_success", "failed", "partial_success"),
        help="Receipt result; verified_success requires every criterion to pass.",
    )
    verification_receipt_parser.add_argument(
        "--actor", required=True, help="Human/operator identity recorded on the receipt."
    )
    verification_receipt_parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of a compact receipt line."
    )
    signal_repair_parser = subparsers.add_parser(
        "signal-repair",
        help="Explicitly reset one malformed claimed Signal to pending with an audit receipt.",
    )
    signal_repair_parser.add_argument("--id", required=True, help="Exact malformed Signal id to repair.")
    signal_repair_parser.add_argument("--reason", required=True, help="Required operator repair reason.")
    signal_repair_parser.add_argument("--actor", default=None, help="Optional operator identity for the receipt.")
    signal_repair_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")

    db_health_parser = subparsers.add_parser(
        "db-health",
        help="Inspect SQLite integrity, foreign keys, persisted JSON/vector state, and capacity metrics.",
    )
    db_health_parser.add_argument(
        "--full", action="store_true", help="Run full integrity_check instead of quick_check."
    )
    db_health_parser.add_argument(
        "--repair-projections",
        action="store_true",
        help="Rebuild content hashes, typed projections, edges, tags, and FTS before inspection.",
    )
    db_health_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")

    backup_parser = subparsers.add_parser("backup", help="Create a consistent SQLite backup using the backup API.")
    backup_parser.add_argument("--output", type=Path, required=True, help="Backup database output path.")
    backup_parser.add_argument("--force", action="store_true", help="Allow replacing an existing backup file.")
    backup_parser.add_argument("--full-verify", action="store_true", help="Run full integrity_check on the backup.")
    backup_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")

    verify_backup_parser = subparsers.add_parser("verify-backup", help="Verify a backup without restoring it.")
    verify_backup_parser.add_argument("--input", type=Path, required=True, help="Backup database path.")
    verify_backup_parser.add_argument(
        "--quick", action="store_true", help="Use quick_check instead of full integrity_check."
    )
    verify_backup_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")

    restore_parser = subparsers.add_parser("restore", help="Restore a verified backup to the configured database path.")
    restore_parser.add_argument("--input", type=Path, required=True, help="Backup database path.")
    restore_parser.add_argument(
        "--target",
        type=Path,
        default=resolve_bridge_db_path(),
        help="Target database path. Defaults to the configured bridge database.",
    )
    restore_parser.add_argument("--force", action="store_true", help="Replace an existing target after preserving it.")
    restore_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")

    checkpoint_parser = subparsers.add_parser("wal-checkpoint", help="Run an explicit SQLite WAL checkpoint.")
    checkpoint_parser.add_argument(
        "--mode",
        choices=("passive", "full", "restart", "truncate"),
        default="passive",
        help="SQLite checkpoint mode.",
    )
    checkpoint_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")

    cleanup_parser = subparsers.add_parser(
        "signal-cleanup",
        help="Preview or apply retention cleanup for old acknowledged and expired Signals.",
    )
    cleanup_parser.add_argument("--acked-older-than-days", type=float, default=30.0)
    cleanup_parser.add_argument("--expired-older-than-days", type=float, default=7.0)
    cleanup_parser.add_argument("--limit", type=int, default=1_000)
    cleanup_parser.add_argument("--apply", action="store_true", help="Delete matching Signals; default is dry-run.")
    cleanup_parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")

    run_consolidation_parser = subparsers.add_parser(
        "consolidate-runs",
        help="Read completed run evidence and emit shadow-only lesson candidates.",
    )
    run_consolidation_parser.add_argument("--shadow", action="store_true", help="Required safety acknowledgement.")
    run_consolidation_parser.add_argument("--workspace-key", required=True, help="Workspace key to inspect.")
    run_consolidation_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Internal read-only page size (1..500); the command scans the full workspace.",
    )
    run_consolidation_parser.add_argument(
        "--stage",
        action="store_true",
        help="Stage eligible results only in the hidden learning-candidate review lane.",
    )
    run_consolidation_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format.",
    )
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


def _run_setup(namespace: argparse.Namespace) -> int:
    if namespace.yes and not namespace.apply:
        print("setup --yes requires --apply", file=sys.stderr)
        return 2
    if namespace.apply and namespace.rollback:
        print("setup --apply and --rollback cannot be combined", file=sys.stderr)
        return 2
    if namespace.rollback and namespace.json:
        print("setup --rollback requires interactive human confirmation and cannot use --json", file=sys.stderr)
        return 2

    def build_plan():
        return build_setup_plan(
            clients=namespace.client,
            cwd=namespace.cwd,
            python_path=namespace.python_path,
            bridge_home=namespace.bridge_home,
            bridge_config_path=namespace.config_path,
        )

    plan = build_plan()
    if namespace.rollback:
        print(render_setup_apply_confirmation(plan))
        if not _confirm_setup_mutation("Rollback P2B-owned changes? [y/N] "):
            print("No changes were made.")
            return 0
        rollback_result = rollback_setup_plan(plan)
        print(render_setup_rollback_result(rollback_result))
        return 1 if any(client.status == "failed" for client in rollback_result.clients) else 0

    if not namespace.apply:
        if namespace.json:
            print(json.dumps(plan.as_dict(), indent=2, sort_keys=True))
        else:
            print(render_setup_plan(plan))
        return 0

    if namespace.json and not namespace.yes:
        print("setup --apply --json requires --yes to avoid an interactive machine-readable prompt", file=sys.stderr)
        return 2

    snapshot = capture_setup_apply_snapshot(plan)
    if not namespace.yes:
        print(render_setup_apply_confirmation(plan))
        if not _confirm_setup_mutation("Apply these changes? [y/N] "):
            print("No changes were made.")
            return 0

    apply_result = apply_setup_plan(plan, current_plan=build_plan(), snapshot=snapshot)
    if namespace.json:
        print(json.dumps(apply_result.as_dict(), indent=2, sort_keys=True))
    else:
        print(render_setup_apply_result(apply_result))
    return 1 if any(client.status == "failed" for client in apply_result.clients) else 0


def _confirm_setup_mutation(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def _run_first_run(namespace: argparse.Namespace) -> int:
    store = MemoryStore.from_env()
    report = build_first_run_report(
        store,
        client=namespace.client,
        namespace=namespace.namespace,
        query=namespace.query,
        python_path=namespace.python_path,
        cwd=namespace.cwd,
        bridge_home=namespace.bridge_home,
        config_path=namespace.config_path,
        example=namespace.example,
    )
    if namespace.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(render_first_run_markdown(report))
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


def _run_service(namespace: argparse.Namespace) -> int:
    from .service import run_service

    try:
        result = run_service(
            once=namespace.once,
            allow_multiple_services=namespace.allow_multiple_services,
        )
    except ServiceLockConflict as exc:
        print(f"agent-memory-bridge: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"agent-memory-bridge: service configuration error: {exc}", file=sys.stderr)
        return 2
    if namespace.once and result is not None and any(item.get("status") == "failed" for item in result.values()):
        return 1
    return 0


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
    try:
        with ServiceFileLock(resolve_bridge_home() / "service.lock"):
            store = MemoryStore.from_env()
            with store._connect() as conn:
                if rebuild_fts:
                    report = rebuild_fts_index(conn)
                else:
                    report = inspect_indexes(conn)
                conn.commit()
            if rebuild_embeddings:
                with store._connect() as conn:
                    report = rebuild_embedding_index(conn)
                    conn.commit()
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"agent-memory-bridge: index rebuild failed: {exc}", file=sys.stderr)
        return 1
    if namespace.json:
        print(json.dumps(report, indent=2))
    else:
        print(_render_index_health(report))
    healthy = bool(report["fts"]["healthy"])
    if rebuild_embeddings:
        healthy = healthy and bool(report["embeddings"]["healthy"])
    return 0 if healthy else 1


def _run_bootstrap_repo(namespace: argparse.Namespace) -> int:
    snapshot = compile_repository_snapshot(namespace.path)
    if namespace.namespace:
        store = RepositorySnapshotStore(resolve_repository_snapshot_root())
        stored = store.save_snapshot(snapshot)
        if snapshot.get("binding") != "git_commit":
            stored["binding_action"] = "not_bound"
        else:
            stored["binding_action"] = store.bind_namespace(
                namespace.namespace,
                str(stored["repository_id"]),
                allow_rebind=namespace.rebind,
            )
        snapshot = stored
    if namespace.format == "json":
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        print(render_snapshot_markdown(snapshot), end="")
        if namespace.namespace:
            action = snapshot.get("binding_action")
            print(f"Namespace binding: {json.dumps(action, sort_keys=True)}")
    return 0


def _run_unbind_repo(namespace: argparse.Namespace) -> int:
    store = RepositorySnapshotStore(resolve_repository_snapshot_root())
    removed = store.unbind_namespace(namespace.namespace)
    result = {"namespace": namespace.namespace.strip(), "unbound": removed, "memory_unchanged": True}
    if namespace.format == "json":
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Namespace: {result['namespace']}\nUnbound: {str(removed).lower()}\nMemory unchanged: true")
    return 0


def _run_explore(namespace: argparse.Namespace) -> int:
    if getattr(namespace, "technical", False) and namespace.format == "json":
        print("--technical is only valid with --format markdown", file=sys.stderr)
        return 2
    try:
        build = _build_explorer(
            namespace=namespace.namespace,
            snapshot_root=resolve_repository_snapshot_root(),
            limit=namespace.limit,
        )
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"agent-memory-bridge: explore failed: {exc}", file=sys.stderr)
        return 1
    if namespace.format == "json":
        print(json.dumps(build.projection, indent=2, sort_keys=True))
    elif getattr(namespace, "technical", False):
        print(render_explorer_technical_markdown(build.projection), end="")
    else:
        print(render_explorer_human_markdown(build), end="")
    return 0


def _run_inspect(namespace: argparse.Namespace) -> int:
    store = MemoryStore.from_env()
    repository_store = RepositorySnapshotStore(resolve_repository_snapshot_root())
    repository_snapshot = repository_store.load_bound_snapshot(namespace.namespace)
    report = build_memory_inspect_report(
        store,
        namespace=namespace.namespace,
        query=namespace.query,
        technical=namespace.technical,
        repository_snapshot=repository_snapshot,
    )
    if namespace.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_memory_inspect_markdown(report), end="")
    return 0


def _run_review_queue(namespace: argparse.Namespace) -> int:
    store = MemoryStore.from_env()
    report = build_review_queue_report(
        store,
        namespace=namespace.namespace,
        limit=namespace.limit,
        include_closed=namespace.include_closed,
    )
    if namespace.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(render_review_queue_markdown(report))
    return 0


def _run_review_workflow(namespace: argparse.Namespace) -> int:
    store = MemoryStore.from_env()
    report = build_review_workflow_report(
        store,
        namespace=namespace.namespace,
        limit=namespace.limit,
        include_closed=namespace.include_closed,
    )
    if namespace.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(render_review_workflow_markdown(report))
    return 0


def _run_task_brief(namespace: argparse.Namespace) -> int:
    store = MemoryStore.from_env()
    report = build_task_brief_report(
        store,
        query=namespace.query,
        namespace=namespace.namespace,
        global_namespace=namespace.global_namespace,
        review_limit=namespace.review_limit,
        signal_limit=namespace.signal_limit,
    )
    if namespace.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(render_task_brief_markdown(report))
    return 0


def _run_activation_receipt(namespace: argparse.Namespace) -> int:
    receipt = build_activation_receipt_from_db(
        resolve_bridge_db_path(),
        namespace=namespace.namespace,
        correlation_id=namespace.correlation_id,
    )
    if namespace.format == "json":
        print(json.dumps(receipt, indent=2))
    else:
        print(render_activation_receipt_markdown(receipt))
    return 0 if receipt["status"] == "pass" else 1


def _run_mint_verification_receipt(namespace: argparse.Namespace) -> int:
    try:
        criterion_results = json.loads(namespace.criterion_results_json)
        evidence = json.loads(namespace.evidence_json)
    except json.JSONDecodeError as exc:
        print(f"agent-memory-bridge: invalid receipt JSON: {exc.msg}", file=sys.stderr)
        return 2
    if not isinstance(criterion_results, list) or not isinstance(evidence, list):
        print("agent-memory-bridge: receipt JSON inputs must be arrays", file=sys.stderr)
        return 2
    try:
        receipt = MemoryStore.from_env().mint_operator_verification_receipt(
            workspace_key=namespace.workspace_key,
            run_id=namespace.run_id,
            preflight_event_id=namespace.preflight_event_id,
            evaluator_digest=namespace.evaluator_digest,
            evaluator_version=namespace.evaluator_version,
            criterion_results=criterion_results,
            result=namespace.result,
            evidence=evidence,
            actor=namespace.actor,
        )
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"agent-memory-bridge: verification receipt minting failed: {exc}", file=sys.stderr)
        return 1
    if namespace.json:
        print(json.dumps(receipt, indent=2))
    else:
        print(
            f"verification_receipt_id={receipt['verification_receipt_id']} "
            f"run_id={receipt['run_id']} result={receipt['result']}"
        )
    return 0


def _run_signal_repair(namespace: argparse.Namespace) -> int:
    store = MemoryStore.from_env()
    report = store.repair_signal(namespace.id, reason=namespace.reason, actor=namespace.actor)
    if namespace.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"signal_id={report['id']} repaired={str(report['repaired']).lower()} reason={report['reason']}")
    return 0 if report["repaired"] or report["reason"] == "no-repair-needed" else 1


def _run_db_health(namespace: argparse.Namespace) -> int:
    repair_report = None
    if namespace.repair_projections:
        try:
            repair_report = rebuild_database_projections(
                resolve_bridge_db_path(),
                service_lock_path=resolve_bridge_home() / "service.lock",
            )
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            print(f"agent-memory-bridge: projection repair failed: {exc}", file=sys.stderr)
            return 1
    report = inspect_database(
        resolve_bridge_db_path(),
        full=namespace.full,
        log_dir=resolve_bridge_log_dir(),
    )
    if repair_report is not None:
        report["projection_repair"] = {
            "rebuilt_count": repair_report["rebuilt_count"],
            "service_lock_path": repair_report["service_lock_path"],
        }
    _print_maintenance_report(report, as_json=namespace.json)
    return 0 if report["ok"] else 1


def _run_backup(namespace: argparse.Namespace) -> int:
    try:
        report = backup_database(
            resolve_bridge_db_path(),
            namespace.output,
            force=namespace.force,
            full_verify=namespace.full_verify,
        )
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"agent-memory-bridge: backup failed: {exc}", file=sys.stderr)
        return 1
    _print_maintenance_report(report, as_json=namespace.json)
    return 0


def _run_verify_backup(namespace: argparse.Namespace) -> int:
    report = verify_backup(namespace.input, full=not namespace.quick)
    _print_maintenance_report(report, as_json=namespace.json)
    return 0 if report["ok"] else 1


def _run_restore(namespace: argparse.Namespace) -> int:
    try:
        report = restore_database(
            namespace.input,
            namespace.target,
            force=namespace.force,
            service_lock_path=resolve_bridge_home() / "service.lock",
        )
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"agent-memory-bridge: restore failed: {exc}", file=sys.stderr)
        return 1
    _print_maintenance_report(report, as_json=namespace.json)
    return 0


def _run_wal_checkpoint(namespace: argparse.Namespace) -> int:
    try:
        report = checkpoint_database(resolve_bridge_db_path(), mode=namespace.mode)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"agent-memory-bridge: WAL checkpoint failed: {exc}", file=sys.stderr)
        return 1
    _print_maintenance_report(report, as_json=namespace.json)
    return 0 if report["ok"] else 1


def _run_signal_cleanup(namespace: argparse.Namespace) -> int:
    try:
        report = cleanup_signals(
            resolve_bridge_db_path(),
            acked_older_than_days=namespace.acked_older_than_days,
            expired_older_than_days=namespace.expired_older_than_days,
            limit=namespace.limit,
            apply=namespace.apply,
        )
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"agent-memory-bridge: signal cleanup failed: {exc}", file=sys.stderr)
        return 1
    _print_maintenance_report(report, as_json=namespace.json)
    if not namespace.json:
        print(f"candidate_count={report['candidate_count']}")
        print(f"deleted_count={report['deleted_count']}")
        print(f"applied={str(bool(report['applied'])).lower()}")
    return 0


def _run_consolidate_runs(namespace: argparse.Namespace) -> int:
    if namespace.stage and not namespace.shadow:
        print("agent-memory-bridge: --stage requires --shadow", file=sys.stderr)
        return 2
    if not namespace.shadow:
        print("agent-memory-bridge: consolidate-runs requires --shadow", file=sys.stderr)
        return 2
    try:
        connection = _open_existing_database_read_only(resolve_bridge_db_path())
        try:
            report = build_run_consolidation_report(
                None,
                workspace_key=namespace.workspace_key,
                limit=namespace.limit,
                connection=connection,
            )
        finally:
            connection.close()
        if namespace.stage:
            stage_run_consolidation_report(MemoryStore.from_env(), report)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"agent-memory-bridge: run consolidation failed: {exc}", file=sys.stderr)
        return 2
    if namespace.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_run_consolidation_markdown(report))
    return 0


def _open_existing_database_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"run consolidation requires an existing database: {db_path}")
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
    except BaseException:
        connection.close()
        raise
    return connection


def _print_maintenance_report(report: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2))
        return
    print(f"ok={str(bool(report.get('ok'))).lower()}")
    for key in ("db_path", "source_db", "output", "source_backup", "target_db", "recovery_backup", "mode"):
        if report.get(key) is not None:
            print(f"{key}={report[key]}")


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
    # A source checkout's manifest is authoritative during release preparation.
    # Installed editable metadata can lag after a local version bump.
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject_path.exists():
        with pyproject_path.open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    try:
        return version("agent-memory-bridge")
    except PackageNotFoundError:
        return "0.0.0"
