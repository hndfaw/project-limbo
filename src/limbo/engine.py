"""Local Limbo executor."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from limbo.artifacts import Artifact, ArtifactStore, hash_file
from limbo.cache import CacheEntry, TaskCache
from limbo.errors import ExecutionError
from limbo.fingerprint import outputs_exist, task_fingerprint
from limbo.observability import EventLog, RunMetrics, redact_env
from limbo.graph import downstream_tasks
from limbo.operators import OperatorError, run_operator
from limbo.spec import PipelineSpec, TaskSpec


@dataclass(frozen=True)
class AttemptResult:
    """The outcome of a single execution attempt for a task."""

    number: int
    status: str
    started_at: float
    finished_at: float
    returncode: Optional[int] = None
    reason: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return self.finished_at - self.started_at


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
    attempts: Tuple[AttemptResult, ...] = ()
    artifacts: Tuple[Artifact, ...] = ()
    queued_at: Optional[float] = None

    @property
    def duration_seconds(self) -> float:
        return self.finished_at - self.started_at


@dataclass(frozen=True)
class RunResult:
    run_id: str
    results: List[TaskResult]
    resumed_from: Optional[str] = None

    @property
    def failed(self) -> List[TaskResult]:
        return [result for result in self.results if result.status == "failed"]

    @property
    def blocked(self) -> List[TaskResult]:
        return [result for result in self.results if result.status == "blocked"]

    @property
    def skipped(self) -> List[TaskResult]:
        return [result for result in self.results if result.status == "skipped"]

    @property
    def succeeded(self) -> List[TaskResult]:
        return [result for result in self.results if result.status == "succeeded"]

    def failure_summary(self) -> str:
        """Human-readable explanation of final failures and their attempt history."""

        lines: List[str] = []
        for result in self.failed:
            attempts = result.attempts or ()
            detail = result.reason or (
                f"exit code {result.returncode}" if result.returncode is not None else "failed"
            )
            lines.append(f"{result.task_id}: {detail} after {max(1, len(attempts))} attempt(s)")
            for attempt in attempts:
                outcome = attempt.reason or (
                    f"exit code {attempt.returncode}" if attempt.returncode is not None else attempt.status
                )
                lines.append(f"    attempt {attempt.number}: {attempt.status} ({outcome})")
        for result in self.blocked:
            lines.append(f"{result.task_id}: blocked ({result.reason})")
        return "\n".join(lines)


class LocalExecutor:
    """Execute a Limbo pipeline on the local machine."""

    def __init__(self, state_dir: Path, max_workers: Optional[int] = None,
                 artifact_store: Optional[ArtifactStore] = None) -> None:
        self.state_dir = Path(state_dir)
        self.cache = TaskCache(self.state_dir)
        self.max_workers = max_workers or max(1, min(32, (os.cpu_count() or 1) + 4))
        self.artifact_store = artifact_store

    def plan_status(self, pipeline: PipelineSpec, force: bool = False) -> Dict[str, str]:
        """Return cached or pending for each task without running commands."""

        status: Dict[str, str] = {}
        for task in pipeline.tasks:
            fingerprint = task_fingerprint(task, pipeline.base_dir)
            entry = self.cache.get(task.id)
            if not force and self._cache_hit(task, entry, fingerprint, pipeline.base_dir):
                status[task.id] = "cached"
            else:
                status[task.id] = "pending"
        return status

    def _cache_hit(self, task: TaskSpec, entry: Optional[CacheEntry], fingerprint: str, base_dir: Path) -> bool:
        """Whether a task can be skipped: succeeded, unchanged, and outputs intact.

        When an artifact store is configured and the cache entry recorded output
        digests, validate the outputs by digest (detecting silent edits or
        corruption); otherwise fall back to mere output existence.
        """

        if not entry or entry.status != "succeeded" or entry.fingerprint != fingerprint:
            return False
        if self.artifact_store is not None and entry.artifacts:
            return self._outputs_match_digests(entry, base_dir)
        return outputs_exist(task, base_dir)

    def _outputs_match_digests(self, entry: CacheEntry, base_dir: Path) -> bool:
        for logical_path, digest in entry.artifacts:
            path = _resolve_output(base_dir, logical_path)
            if not path.is_file() or hash_file(path) != digest:
                return False
        return True

    def run(self, pipeline: PipelineSpec, force: bool = False, dry_run: bool = False,
            resumed_from: Optional[str] = None) -> RunResult:
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
            return RunResult(run_id=run_id, results=results, resumed_from=resumed_from)

        events = EventLog(run_dir / "events.jsonl")
        events.emit("run_started", run_id=run_id, task_count=len(pipeline.tasks), resumed_from=resumed_from)
        results = self._run_graph(pipeline, run_id, run_dir, force, events)
        run_result = RunResult(run_id=run_id, results=results, resumed_from=resumed_from)
        events.emit("run_finished", run_id=run_id,
                    status="failed" if run_result.failed else "succeeded")
        self._write_manifest(run_dir, run_result, pipeline)

        if run_result.failed:
            failed_ids = ", ".join(result.task_id for result in run_result.failed)
            raise ExecutionError(f"pipeline failed: {failed_ids}", run_result=run_result)

        return run_result

    def resume(self, run_id: str, force: bool = False) -> RunResult:
        """Resume a prior run, re-executing only incomplete or failed work.

        The prior run's manifest supplies the pipeline path; previously
        succeeded tasks are carried forward through the deterministic cache,
        so only failed, blocked, or never-run tasks (whose dependencies are
        satisfied) execute again.
        """

        manifest = self._read_manifest(run_id)
        pipeline_path = manifest.get("pipeline")
        if not pipeline_path:
            raise ExecutionError(f"run {run_id!r} manifest does not record a pipeline path to resume")
        path = Path(pipeline_path)
        if not path.exists():
            raise ExecutionError(f"cannot resume run {run_id!r}: pipeline {pipeline_path} no longer exists")

        from limbo.spec import load_pipeline

        pipeline = load_pipeline(path)
        return self.run(pipeline, force=force, resumed_from=run_id)

    def list_runs(self, limit: Optional[int] = None) -> List[Dict[str, object]]:
        """Summarize past runs (newest first) from their manifests.

        Each summary carries the run id, the pipeline path, whether it resumed
        another run, and a per-status task count. Malformed manifests are
        skipped rather than aborting the listing.
        """

        runs_dir = self.state_dir / "runs"
        if not runs_dir.exists():
            return []
        # Order by manifest modification time (newest first). The run id only
        # has second resolution, so sorting by id alone would tie-break on its
        # random suffix rather than on when the run actually finished.
        ranked: List[tuple] = []
        for manifest_path in runs_dir.glob("*/manifest.json"):
            try:
                mtime = manifest_path.stat().st_mtime_ns
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(raw, dict):
                continue
            results = raw.get("results", [])
            counts: Dict[str, int] = {}
            if isinstance(results, list):
                for result in results:
                    if isinstance(result, dict):
                        status = result.get("status")
                        if isinstance(status, str):
                            counts[status] = counts.get(status, 0) + 1
            run_id = raw.get("run_id", manifest_path.parent.name)
            summary = {
                "run_id": run_id,
                "resumed_from": raw.get("resumed_from"),
                "pipeline": raw.get("pipeline"),
                "counts": counts,
            }
            ranked.append((mtime, run_id, summary))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        summaries = [summary for _, _, summary in ranked]
        return summaries if limit is None else summaries[:limit]

    def _read_manifest(self, run_id: str) -> Dict[str, object]:
        manifest_path = self.state_dir / "runs" / run_id / "manifest.json"
        if not manifest_path.exists():
            raise ExecutionError(f"no run found with id {run_id!r} under {self.state_dir}")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ExecutionError(f"could not read manifest for run {run_id!r}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ExecutionError(f"manifest for run {run_id!r} is malformed")
        return raw

    def _run_graph(self, pipeline: PipelineSpec, run_id: str, run_dir: Path, force: bool,
                   events: EventLog) -> List[TaskResult]:
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
        queued_at: Dict[str, float] = {}
        futures: Dict[Future[TaskResult], str] = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while ready or futures:
                while ready and len(futures) < self.max_workers:
                    task_id = ready.pop(0)
                    if task_id in blocked:
                        continue
                    task = tasks_by_id[task_id]
                    queued_at[task_id] = time.time()
                    events.emit("task_queued", task_id=task_id)
                    futures[pool.submit(self._run_task, task, pipeline, run_id, run_dir, force, events)] = task_id

                if not futures:
                    break

                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    task_id = futures.pop(future)
                    result = replace(future.result(), queued_at=queued_at.get(task_id))
                    results.append(result)
                    completed.add(task_id)

                    if result.status == "failed":
                        failed.add(task_id)
                        newly_blocked = downstream_tasks(pipeline.tasks, failed)
                        blocked.update(newly_blocked)
                        for blocked_id in sorted(newly_blocked):
                            if blocked_id not in completed and blocked_id not in [item.task_id for item in results]:
                                now = time.time()
                                events.emit("task_blocked", task_id=blocked_id, reason=f"dependency failed: {task_id}")
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

    def _run_task(self, task: TaskSpec, pipeline: PipelineSpec, run_id: str, run_dir: Path, force: bool,
                  events: EventLog) -> TaskResult:
        fingerprint = task_fingerprint(task, pipeline.base_dir)
        started_at = time.time()

        entry = self.cache.get(task.id)
        if not force and self._cache_hit(task, entry, fingerprint, pipeline.base_dir):
            events.emit("task_skipped", task_id=task.id, reason="cache-hit")
            return TaskResult(
                task_id=task.id,
                status="skipped",
                fingerprint=fingerprint,
                started_at=started_at,
                finished_at=time.time(),
                reason="cache-hit",
            )

        events.emit("task_started", task_id=task.id, env=redact_env(task.env) or None)

        task_dir = run_dir / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = task_dir / "stdout.log"
        stderr_path = task_dir / "stderr.log"

        policy = task.retry
        attempts: List[AttemptResult] = []
        returncode: Optional[int] = None
        reason: Optional[str] = None

        for attempt_number in range(1, policy.max_attempts + 1):
            attempt_started = time.time()
            returncode, timed_out, reason = self._execute_attempt(task, pipeline, stdout_path, stderr_path)
            attempt_status = "succeeded" if returncode == 0 else "failed"
            attempts.append(
                AttemptResult(
                    number=attempt_number,
                    status=attempt_status,
                    started_at=attempt_started,
                    finished_at=time.time(),
                    returncode=returncode,
                    reason=reason,
                )
            )
            if attempt_status == "succeeded":
                break
            has_more = attempt_number < policy.max_attempts
            if not (has_more and policy.is_retryable(returncode, timed_out)):
                break
            delay = policy.delay_for(attempt_number)
            if delay > 0:
                time.sleep(delay)

        status = "succeeded" if returncode == 0 else "failed"
        artifacts = self._ingest_artifacts(task, pipeline.base_dir) if status == "succeeded" else ()
        result = TaskResult(
            task_id=task.id,
            status=status,
            fingerprint=fingerprint,
            started_at=started_at,
            finished_at=time.time(),
            returncode=returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            reason=reason if status == "failed" else None,
            attempts=tuple(attempts),
            artifacts=artifacts,
        )

        if status == "succeeded":
            digests = tuple((art.logical_path, art.digest) for art in artifacts if art.logical_path)
            self.cache.put(CacheEntry(task.id, fingerprint, "succeeded", run_id, digests))

        events.emit(f"task_{status}", task_id=task.id, returncode=returncode,
                    attempts=len(attempts), artifacts=len(artifacts) or None)
        return result

    def _ingest_artifacts(self, task: TaskSpec, base_dir: Path) -> Tuple[Artifact, ...]:
        """Store a succeeded task's declared outputs in the artifact store, if configured."""

        if self.artifact_store is None or not task.outputs:
            return ()
        artifacts = []
        for output in task.outputs:
            path = _resolve_output(base_dir, output)
            if path.is_file():
                artifacts.append(self.artifact_store.put_file(path, producer=task.id, logical_path=output))
        return tuple(artifacts)

    def _execute_attempt(self, task: TaskSpec, pipeline: PipelineSpec, stdout_path: Path,
                         stderr_path: Path) -> Tuple[Optional[int], bool, Optional[str]]:
        """Run a task once, write its logs, and report (returncode, timed_out, reason)."""

        cwd = _task_cwd(pipeline.base_dir, task)
        env = os.environ.copy()
        env.update(task.env)

        try:
            if task.operator is not None:
                count = run_operator(task.operator, pipeline.base_dir)
                stdout_path.write_text(f"wrote {count} row(s)\n", encoding="utf-8")
                stderr_path.write_text("", encoding="utf-8")
                return 0, False, None
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
            reason = None if completed.returncode == 0 else f"exit code {completed.returncode}"
            return completed.returncode, False, reason
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(exc.stdout or "", encoding="utf-8")
            stderr_path.write_text((exc.stderr or "") + f"\nTask timed out after {task.timeout_seconds} seconds.\n", encoding="utf-8")
            return None, True, "timeout"
        except OperatorError as exc:
            stdout_path.write_text("", encoding="utf-8")
            stderr_path.write_text(f"{exc}\n", encoding="utf-8")
            return 1, False, str(exc)

    def _write_manifest(self, run_dir: Path, run_result: RunResult, pipeline: PipelineSpec) -> None:
        payload = {
            "run_id": run_result.run_id,
            "resumed_from": run_result.resumed_from,
            "pipeline": str(pipeline.source_path) if pipeline.source_path else None,
            "metrics": RunMetrics.from_results(run_result.results).to_dict(),
            "results": [
                {
                    "task_id": result.task_id,
                    "status": result.status,
                    "fingerprint": result.fingerprint,
                    "duration_seconds": result.duration_seconds,
                    "queued_at": result.queued_at,
                    "returncode": result.returncode,
                    "stdout_path": str(result.stdout_path) if result.stdout_path else None,
                    "stderr_path": str(result.stderr_path) if result.stderr_path else None,
                    "reason": result.reason,
                    "attempts": [
                        {
                            "number": attempt.number,
                            "status": attempt.status,
                            "returncode": attempt.returncode,
                            "reason": attempt.reason,
                            "duration_seconds": attempt.duration_seconds,
                        }
                        for attempt in result.attempts
                    ],
                    "artifacts": [artifact.to_dict() for artifact in result.artifacts],
                }
                for result in run_result.results
            ],
        }
        (run_dir / "manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_output(base_dir: Path, output: str) -> Path:
    path = Path(output)
    return path if path.is_absolute() else base_dir / path


def _task_cwd(base_dir: Path, task: TaskSpec) -> Path:
    if task.cwd:
        cwd = Path(task.cwd)
        if cwd.is_absolute():
            return cwd
        return (base_dir / cwd).resolve()
    return base_dir


def _run_id() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
