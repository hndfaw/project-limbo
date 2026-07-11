"""Command-line interface for Project Limbo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

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
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a pipeline spec")
    validate.add_argument("pipeline")

    plan = subparsers.add_parser("plan", help="show cache-aware execution plan")
    _common(plan)

    run = subparsers.add_parser("run", help="execute a pipeline")
    _common(run)
    run.add_argument("--dry-run", action="store_true", help="produce the run plan without executing commands")

    resume = subparsers.add_parser("resume", help="resume a prior run, re-executing only incomplete work")
    resume.add_argument("run_id", help="id of the run to resume (see .limbo/runs)")
    resume.add_argument("--state-dir", default=".limbo", help="directory for cache and run metadata")
    resume.add_argument("--max-workers", type=int, default=None, help="maximum parallel tasks")
    resume.add_argument("--force", action="store_true", help="ignore cached successful tasks")
    resume.add_argument("--json", action="store_true", help="emit machine-readable JSON")

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
