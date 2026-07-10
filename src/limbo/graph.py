"""Graph planning utilities for Limbo pipelines."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, Iterable, List, Set

from limbo.spec import PipelineSpec, TaskSpec


@dataclass(frozen=True)
class TaskPlan:
    """A deterministic topological plan."""

    levels: List[List[TaskSpec]]

    @property
    def ordered(self) -> List[TaskSpec]:
        return [task for level in self.levels for task in level]


def build_plan(pipeline: PipelineSpec) -> TaskPlan:
    """Build deterministic topological levels for the pipeline."""

    tasks_by_id = pipeline.task_map
    indegree: Dict[str, int] = {task.id: len(task.needs) for task in pipeline.tasks}
    dependents: Dict[str, Set[str]] = defaultdict(set)

    for task in pipeline.tasks:
        for dep in task.needs:
            dependents[dep].add(task.id)

    ready = deque(sorted(task_id for task_id, degree in indegree.items() if degree == 0))
    levels: List[List[TaskSpec]] = []

    while ready:
        current_ids = list(ready)
        ready.clear()
        levels.append([tasks_by_id[task_id] for task_id in current_ids])

        next_ready = []
        for task_id in current_ids:
            for dependent in sorted(dependents[task_id]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    next_ready.append(dependent)
        ready.extend(sorted(next_ready))

    return TaskPlan(levels=levels)


def downstream_tasks(tasks: Iterable[TaskSpec], failed_ids: Iterable[str]) -> Set[str]:
    """Return all tasks that depend directly or indirectly on failed IDs."""

    failed = set(failed_ids)
    dependents: Dict[str, Set[str]] = defaultdict(set)
    all_tasks = list(tasks)
    for task in all_tasks:
        for dep in task.needs:
            dependents[dep].add(task.id)

    blocked: Set[str] = set()
    queue = deque(failed)
    while queue:
        task_id = queue.popleft()
        for dependent in dependents.get(task_id, set()):
            if dependent not in blocked:
                blocked.add(dependent)
                queue.append(dependent)
    return blocked
