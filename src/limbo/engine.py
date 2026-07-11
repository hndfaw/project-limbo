"""Local Limbo executor."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from limbo.cache import CacheEntry, TaskCache
from limbo.errors import ExecutionError
from limbo.fingerprint import outputs_exist, task_fingerprint
from limbo.graph import downstream_tasks
from limbo.operators import OperatorError, run_operator
from limbo.spec import PipelineSpec, TaskSpec


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    status: str
    fingerprint: str
    started_at: float
    finished_at: float
    returncode: Optional[int] = None
    stdout_path: Optional[Path] = None
    stderr_path: Optional[Path] = None
    reason: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return self.finished_at - self.started_at


@dataclass(frozen=True)
class RunResult:
    run_id: str
    results: List[TaskResult]

    @property
    def failed(self) -> List[TaskResult]:
        return [result for result in self.results if result.status == "failed"]

    @property
    def skipped(self) -> List[TaskResult]:
        return [result for result in self.results if result.status == "skipped"]

    @property
    def succeeded(self) -> List[TaskResult]:
        return [result for result in self.results if result.status == "succeeded"]


class LocalExecutor:
    """Execute a Limbo pipeline on the local machine."""

    def __init__(self, state_dir: Path, max_workers: Optional[int] = None) -> None:
        self.state_dir = Path(state_dir)
        self.cache = TaskCache(self.state_dir)
        self.max_workers = max_workers or max(1, min(32, (os.cpu_count() or 1) + 4))

    def plan_status(self, pipeline: PipelineSpec, force: bool = False) -> Dict[str, str]:
        """Return cached or pending for each task without running commands."""

        status: Dict[str, str] = {}
        for task in pipeline.tasks:
            fingerprint = task_fingerprint(task, pipeline.base_dir)
            entry = self.cache.get(task.id)
            if not force and entry and entry.status == "succeeded" and entry.fingerprint == fingerprint and outputs_exist(task, pipeline.base_dir):
                status[task.id] = "cached"
            else:
                status[task.id] = "pending"
        return status

    def run(self, pipeline: PipelineSpec, force: bool = False, dry_run: bool = False) -> RunResult:
        """Run a pipeline with dependency-aware local scheduling."""

        run_id = _run_id()
        run_dir = self.state_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        if dry_run:
            now = time.time()
            results = [
                TaskResult(
                    task_id=task.id,
                    status=self.plan_status(pipeline, force=force)[task.id],
                    fingerprint=task_fingerprint(task, pipeline.base_dir),
                    started_at=now,
                    finished_at=now,
                    reason="dry-run",
                )
                for task in pipeline.tasks
            ]
            return RunResult(run_id=run_id, results=results)

        results = self._run_graph(pipeline, run_id, run_dir, force)
        run_result = RunResult(run_id=run_id, results=results)
        self._write_manifest(run_dir, run_result)

        if run_result.failed:
            failed_ids = ", ".join(result.task_id for result in run_result.failed)
            raise ExecutionError(f"pipeline failed: {failed_ids}")

        return run_result

    def _run_graph(self, pipeline: PipelineSpec, run_id: str, run_dir: Path, force: bool) -> List[TaskResult]:
        tasks_by_id = pipeline.task_map
        remaining_deps: Dict[str, Set[str]] = {task.id: set(task.needs) for task in pipeline.tasks}
        dependents: Dict[str, Set[str]] = {task.id: set() for task in pipeline.tasks}
        for task in pipeline.tasks:
            for dep in task.needs:
                dependents[dep].add(task.id)

        ready = sorted(task.id for task in pipeline.tasks if not remaining_deps[task.id])
        completed: Set[str] = set()
        failed: Set[str] = set()
        blocked: Set[str] = set()
        results: List[TaskResult] = []
        futures: Dict[Future[TaskResult], str] = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while ready or futures:
                while ready and len(futures) < self.max_workers:
                    task_id = ready.pop(0)
                    if task_id in blocked:
                        continue
                    task = tasks_by_id[task_id]
                    futures[pool.submit(self._run_task, task, pipeline, run_id, run_dir, force)] = task_id

                if not futures:
                    break

                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    task_id = futures.pop(future)
                    result = future.result()
                    results.append(result)
                    completed.add(task_id)

                    if result.status == "failed":
                        failed.add(task_id)
                        newly_blocked = downstream_tasks(pipeline.tasks, failed)
                        blocked.update(newly_blocked)
                        for blocked_id in sorted(newly_blocked):
                            if blocked_id not in completed and blocked_id not in [item.task_id for item in results]:
                                now = time.time()
                                results.append(
                                    TaskResult(
                                        task_id=blocked_id,
                                        status="blocked",
                                        fingerprint=task_fingerprint(tasks_by_id[blocked_id], pipeline.base_dir),
                                        started_at=now,
                                        finished_at=now,
                                        reason=f"dependency failed: {task_id}",
                                    )
                                )
                                completed.add(blocked_id)
                        ready = [item for item in ready if item not in blocked]
                        continue

                    for dependent in sorted(dependents[task_id]):
                        if dependent in completed or dependent in blocked:
                            continue
                        remaining_deps[dependent].discard(task_id)
                        if not remaining_deps[dependent] and dependent not in ready:
                            ready.append(dependent)
                    ready.sort()

        return sorted(results, key=lambda result: [task.id for task in pipeline.tasks].index(result.task_id))

    def _run_task(self, task: TaskSpec, pipeline: PipelineSpec, run_id: str, run_dir: Path, force: bool) -> TaskResult:
        fingerprint = task_fingerprint(task, pipeline.base_dir)
        started_at = time.time()

        entry = self.cache.get(task.id)
        if not force and entry and entry.status == "succeeded" and entry.fingerprint == fingerprint and outputs_exist(task, pipeline.base_dir):
            return TaskResult(
                task_id=task.id,
                status="skipped",
                fingerprint=fingerprint,
                started_at=started_at,
                finished_at=time.time(),
                reason="cache-hit",
            )

        task_dir = run_dir / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = task_dir / "stdout.log"
        stderr_path = task_dir / "stderr.log"
        cwd = _task_cwd(pipeline.base_dir, task)
        env = os.environ.copy()
        env.update(task.env)

        try:
            if task.operator is not None:
                count = run_operator(task.operator, pipeline.base_dir)
                stdout_path.write_text(f"wrote {count} row(s)\n", encoding="utf-8")
                stderr_path.write_text("", encoding="utf-8")
                completed = subprocess.CompletedProcess([], 0, "", "")
            else:
                completed = subprocess.run(
                    task.command,
                    cwd=str(cwd),
                    env=env,
                    shell=True,
                    text=True,
                    capture_output=True,
                    timeout=task.timeout_seconds,
                )
                stdout_path.write_text(completed.stdout, encoding="utf-8")
                stderr_path.write_text(completed.stderr, encoding="utf-8")
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(exc.stdout or "", encoding="utf-8")
            stderr_path.write_text((exc.stderr or "") + f"\nTask timed out after {task.timeout_seconds} seconds.\n", encoding="utf-8")
            return TaskResult(
                task_id=task.id,
                status="failed",
                fingerprint=fingerprint,
                started_at=started_at,
                finished_at=time.time(),
                returncode=None,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                reason="timeout",
            )

        except OperatorError as exc:
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(f"{exc}\n", encoding="utf-8")
            completed = subprocess.CompletedProcess([], 1, "", str(exc))

        status = "succeeded" if completed.returncode == 0 else "failed"
        result = TaskResult(
            task_id=task.id,
            status=status,
            fingerprint=fingerprint,
            started_at=started_at,
            finished_at=time.time(),
            returncode=completed.returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

        if status == "succeeded":
            self.cache.put(CacheEntry(task.id, fingerprint, "succeeded", run_id))

        return result

    def _write_manifest(self, run_dir: Path, run_result: RunResult) -> None:
        payload = {
            "run_id": run_result.run_id,
            "results": [
                {
                    "task_id": result.task_id,
                    "status": result.status,
                    "fingerprint": result.fingerprint,
                    "duration_seconds": result.duration_seconds,
                    "returncode": result.returncode,
                    "stdout_path": str(result.stdout_path) if result.stdout_path else None,
                    "stderr_path": str(result.stderr_path) if result.stderr_path else None,
                    "reason": result.reason,
                }
                for result in run_result.results
            ],
        }
        (run_dir / "manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _task_cwd(base_dir: Path, task: TaskSpec) -> Path:
    if task.cwd:
        cwd = Path(task.cwd)
        if cwd.is_absolute():
            return cwd
        return (base_dir / cwd).resolve()
    return base_dir


def _run_id() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
