"""Command-line interface for Project Limbo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

from limbo import __version__
from limbo.engine import LocalExecutor, RunResult
from limbo.errors import ExecutionError, LimboError
from limbo.graph import build_plan
from limbo.spec import load_pipeline


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.command == "validate":
            pipeline = load_pipeline(Path(args.pipeline))
            print(f"valid: {len(pipeline.tasks)} task(s)")
            return 0
        if args.command == "plan":
            pipeline = load_pipeline(Path(args.pipeline))
            executor = LocalExecutor(Path(args.state_dir), max_workers=args.max_workers)
            statuses = executor.plan_status(pipeline, force=args.force)
            plan = build_plan(pipeline)
            _print_plan(plan.levels, statuses, json_output=args.json)
            return 0
        if args.command == "run":
            pipeline = load_pipeline(Path(args.pipeline))
            executor = LocalExecutor(Path(args.state_dir), max_workers=args.max_workers)
            result = executor.run(pipeline, force=args.force, dry_run=args.dry_run)
            _print_run(result, json_output=args.json)
            return 0
        if args.command == "resume":
            executor = LocalExecutor(Path(args.state_dir), max_workers=args.max_workers)
            result = executor.resume(args.run_id, force=args.force)
            _print_run(result, json_output=args.json)
            return 0
        if args.command == "runs":
            executor = LocalExecutor(Path(args.state_dir))
            _print_runs(executor.list_runs(limit=args.limit), json_output=args.json)
            return 0
        if args.command == "inspect":
            _inspect(Path(args.state_dir), args.run_id, json_output=args.json)
            return 0
        if args.command == "timeline":
            _timeline(Path(args.state_dir), args.run_id, json_output=args.json)
            return 0
    except ExecutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        _print_failure_summary(exc, json_output=getattr(args, "json", False))
        return 2
    except LimboError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="limbo", description="Run reproducible local DAG pipelines.")
    parser.add_argument("--version", action="version", version=f"limbo {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a pipeline spec")
    validate.add_argument("pipeline")

    plan = subparsers.add_parser("plan", help="show cache-aware execution plan")
    _common(plan)

    run = subparsers.add_parser("run", help="execute a pipeline")
    _common(run)
    run.add_argument("--dry-run", action="store_true", help="produce the run plan without executing commands")

    resume = subparsers.add_parser("resume", help="resume a prior run, re-executing only incomplete work")
    resume.add_argument("run_id", help="id of the run to resume (see 'limbo runs')")
    resume.add_argument("--state-dir", default=".limbo", help="directory for cache and run metadata")
    resume.add_argument("--max-workers", type=int, default=None, help="maximum parallel tasks")
    resume.add_argument("--force", action="store_true", help="ignore cached successful tasks")
    resume.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    runs = subparsers.add_parser("runs", help="list past runs and their task-status counts")
    runs.add_argument("--state-dir", default=".limbo", help="directory for cache and run metadata")
    runs.add_argument("--limit", type=int, default=20, help="maximum number of runs to show (newest first)")
    runs.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    inspect = subparsers.add_parser("inspect", help="summarize a run's manifest and metrics")
    inspect.add_argument("run_id", help="id of the run to inspect (see 'limbo runs')")
    inspect.add_argument("--state-dir", default=".limbo", help="directory for cache and run metadata")
    inspect.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    timeline = subparsers.add_parser("timeline", help="show a readable execution timeline for a run")
    timeline.add_argument("run_id", help="id of the run to show (see 'limbo runs')")
    timeline.add_argument("--state-dir", default=".limbo", help="directory for cache and run metadata")
    timeline.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("pipeline")
    parser.add_argument("--state-dir", default=".limbo", help="directory for cache and run metadata")
    parser.add_argument("--max-workers", type=int, default=None, help="maximum parallel tasks")
    parser.add_argument("--force", action="store_true", help="ignore cached successful tasks")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def _print_plan(levels, statuses, json_output: bool) -> None:
    if json_output:
        print(
            json.dumps(
                {
                    "levels": [
                        [{"id": task.id, "status": statuses[task.id]} for task in level]
                        for level in levels
                    ]
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    for index, level in enumerate(levels, start=1):
        print(f"level {index}:")
        for task in level:
            print(f"  {task.id}: {statuses[task.id]}")


def _print_run(result: RunResult, json_output: bool) -> None:
    if json_output:
        print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "tasks": [
                        {
                            "id": item.task_id,
                            "status": item.status,
                            "returncode": item.returncode,
                            "reason": item.reason,
                            "duration_seconds": item.duration_seconds,
                        }
                        for item in result.results
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    header = f"run {result.run_id}"
    if result.resumed_from:
        header += f" (resumed from {result.resumed_from})"
    print(header)
    for item in result.results:
        notes = []
        if item.reason:
            notes.append(item.reason)
        if len(item.attempts) > 1:
            notes.append(f"{len(item.attempts)} attempts")
        detail = f" ({', '.join(notes)})" if notes else ""
        print(f"  {item.task_id}: {item.status}{detail}")


def _print_runs(runs: list, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"runs": runs}, indent=2, sort_keys=True))
        return
    if not runs:
        print("no runs found")
        return
    for run in runs:
        counts = run.get("counts") or {}
        summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items())) or "no tasks"
        line = f"{run['run_id']}: {summary}"
        if run.get("resumed_from"):
            line += f" (resumed from {run['resumed_from']})"
        print(line)


def _load_manifest(state_dir: Path, run_id: str) -> dict:
    path = state_dir / "runs" / run_id / "manifest.json"
    if not path.exists():
        raise ExecutionError(f"no run found with id {run_id!r} under {state_dir}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ExecutionError(f"could not read manifest for run {run_id!r}: {exc}") from exc


def _inspect(state_dir: Path, run_id: str, json_output: bool) -> None:
    manifest = _load_manifest(state_dir, run_id)
    if json_output:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return
    print(f"run {manifest.get('run_id', run_id)}")
    if manifest.get("resumed_from"):
        print(f"  resumed from: {manifest['resumed_from']}")
    if manifest.get("pipeline"):
        print(f"  pipeline: {manifest['pipeline']}")
    metrics = manifest.get("metrics") or {}
    if metrics:
        print("  metrics: " + ", ".join(
            f"{key}={_format_number(metrics[key])}" for key in sorted(metrics)
        ))
    for result in manifest.get("results", []):
        detail = []
        if result.get("returncode") is not None:
            detail.append(f"exit {result['returncode']}")
        detail.append(f"{result.get('duration_seconds', 0.0) * 1000:.0f}ms")
        if len(result.get("attempts", [])) > 1:
            detail.append(f"{len(result['attempts'])} attempts")
        if result.get("artifacts"):
            detail.append(f"{len(result['artifacts'])} artifact(s)")
        if result.get("reason"):
            detail.append(result["reason"])
        print(f"  {result['task_id']}: {result['status']} ({', '.join(detail)})")


def _timeline(state_dir: Path, run_id: str, json_output: bool) -> None:
    from limbo.observability import EventLog, build_timeline

    events = EventLog.read(state_dir / "runs" / run_id / "events.jsonl")
    if not events:
        # Fall back to confirming the run exists so the error is clear.
        _load_manifest(state_dir, run_id)
        print("no events recorded for this run") if not json_output else print(json.dumps({"events": []}))
        return
    if json_output:
        print(json.dumps({"events": events}, indent=2, sort_keys=True))
        return
    print(f"timeline for {run_id}")
    for line in build_timeline(events):
        print(f"  {line}")


def _format_number(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _print_failure_summary(exc: ExecutionError, json_output: bool) -> None:
    run_result = getattr(exc, "run_result", None)
    if run_result is None:
        return
    summary = run_result.failure_summary()
    if not summary:
        return
    if json_output:
        return
    print("failure summary:", file=sys.stderr)
    for line in summary.splitlines():
        print(f"  {line}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
