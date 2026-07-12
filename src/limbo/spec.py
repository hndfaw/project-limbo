"""Pipeline specification parsing and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from limbo.errors import SpecError
from limbo.policy import Policy, parse_policy
from limbo.retry import RetryPolicy, parse_retry_policy


SUPPORTED_VERSION = 1


@dataclass(frozen=True)
class TaskSpec:
    """A single executable task in a Limbo pipeline."""

    id: str
    command: Optional[str] = None
    needs: List[str] = field(default_factory=list)
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    timeout_seconds: Optional[float] = None
    operator: Optional[Dict[str, Any]] = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    sandbox: Optional[str] = None


@dataclass(frozen=True)
class PipelineSpec:
    """A parsed Limbo pipeline."""

    version: int
    tasks: List[TaskSpec]
    base_dir: Path
    source_path: Optional[Path] = None
    policy: Policy = field(default_factory=Policy)

    @property
    def task_map(self) -> Dict[str, TaskSpec]:
        return {task.id: task for task in self.tasks}


def load_pipeline(path: Path) -> PipelineSpec:
    """Load and validate a pipeline JSON file."""

    path = Path(path).resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecError(f"{path}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise SpecError(f"{path}: could not read pipeline: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise SpecError("pipeline root must be a JSON object")

    version = raw.get("version")
    if version != SUPPORTED_VERSION:
        raise SpecError(f"unsupported pipeline version {version!r}; expected {SUPPORTED_VERSION}")

    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise SpecError("pipeline must contain a non-empty tasks list")

    tasks = [_parse_task(item, index) for index, item in enumerate(tasks_raw)]
    policy = parse_policy(raw.get("policy"))
    pipeline = PipelineSpec(version=version, tasks=tasks, base_dir=path.parent, source_path=path, policy=policy)
    validate_pipeline(pipeline)
    return pipeline


def validate_pipeline(pipeline: PipelineSpec) -> None:
    """Validate task IDs, dependencies, and graph shape."""

    seen = set()
    duplicates = set()
    for task in pipeline.tasks:
        if task.id in seen:
            duplicates.add(task.id)
        seen.add(task.id)

    if duplicates:
        raise SpecError(f"duplicate task id(s): {', '.join(sorted(duplicates))}")

    known = {task.id for task in pipeline.tasks}
    missing = []
    for task in pipeline.tasks:
        for dep in task.needs:
            if dep not in known:
                missing.append(f"{task.id}->{dep}")
    if missing:
        raise SpecError(f"missing dependency task(s): {', '.join(sorted(missing))}")

    for task in pipeline.tasks:
        if task.sandbox is not None and task.sandbox not in pipeline.policy.sandbox_profiles:
            raise SpecError(
                f"task {task.id!r}: sandbox {task.sandbox!r} is not defined in policy.sandbox_profiles"
            )

    _ensure_acyclic(pipeline.tasks)


def _parse_task(raw: Any, index: int) -> TaskSpec:
    if not isinstance(raw, Mapping):
        raise SpecError(f"task at index {index} must be an object")

    task_id = _required_string(raw, "id", index)
    command = raw.get("command")
    operator = raw.get("operator")
    if (command is None) == (operator is None):
        raise SpecError(f"task {task_id!r}: specify exactly one of command or operator")
    if command is not None and (not isinstance(command, str) or not command.strip()):
        raise SpecError(f"task at index {index}: command must be a non-empty string")
    if operator is not None:
        from limbo.operators import validate_operator

        operator = validate_operator(operator, task_id)
    needs = _string_list(raw.get("needs", []), "needs", index)
    inputs = _string_list(raw.get("inputs", []), "inputs", index)
    outputs = _string_list(raw.get("outputs", []), "outputs", index)
    if operator is not None:
        from limbo.operators import operator_paths

        operator_inputs, operator_outputs = operator_paths(operator)
        inputs = list(dict.fromkeys(inputs + operator_inputs))
        outputs = list(dict.fromkeys(outputs + operator_outputs))
    env = _string_mapping(raw.get("env", {}), "env", index)
    cwd = raw.get("cwd")
    timeout_seconds = raw.get("timeout_seconds")

    if cwd is not None and not isinstance(cwd, str):
        raise SpecError(f"task {task_id!r}: cwd must be a string")
    if timeout_seconds is not None:
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise SpecError(f"task {task_id!r}: timeout_seconds must be a positive number")
        timeout_seconds = float(timeout_seconds)

    retry = parse_retry_policy(raw.get("retry"), task_id)

    sandbox = raw.get("sandbox")
    if sandbox is not None and (not isinstance(sandbox, str) or not sandbox):
        raise SpecError(f"task {task_id!r}: sandbox must be a non-empty string")

    return TaskSpec(
        id=task_id,
        command=command,
        operator=operator,
        needs=needs,
        inputs=inputs,
        outputs=outputs,
        env=env,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        retry=retry,
        sandbox=sandbox,
    )


def _required_string(raw: Mapping[str, Any], key: str, index: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"task at index {index}: {key} must be a non-empty string")
    return value


def _string_list(value: Any, field_name: str, index: int) -> List[str]:
    if not isinstance(value, list):
        raise SpecError(f"task at index {index}: {field_name} must be a list")
    bad = [item for item in value if not isinstance(item, str) or not item]
    if bad:
        raise SpecError(f"task at index {index}: {field_name} entries must be non-empty strings")
    return list(value)


def _string_mapping(value: Any, field_name: str, index: int) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        raise SpecError(f"task at index {index}: {field_name} must be an object")
    result: Dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise SpecError(f"task at index {index}: {field_name} keys and values must be strings")
        result[key] = item
    return result


def _ensure_acyclic(tasks: Iterable[TaskSpec]) -> None:
    task_map = {task.id: task for task in tasks}
    temporary = set()
    permanent = set()
    path: List[str] = []

    def visit(task_id: str) -> None:
        if task_id in permanent:
            return
        if task_id in temporary:
            cycle_start = path.index(task_id)
            cycle = path[cycle_start:] + [task_id]
            raise SpecError(f"dependency cycle detected: {' -> '.join(cycle)}")

        temporary.add(task_id)
        path.append(task_id)
        for dep in task_map[task_id].needs:
            visit(dep)
        path.pop()
        temporary.remove(task_id)
        permanent.add(task_id)

    for task_id in task_map:
        visit(task_id)
