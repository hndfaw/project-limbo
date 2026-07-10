import unittest
from pathlib import Path

from limbo.graph import build_plan, downstream_tasks
from limbo.spec import PipelineSpec, TaskSpec


class GraphTests(unittest.TestCase):
    def test_build_plan_groups_independent_tasks(self):
        pipeline = PipelineSpec(
            version=1,
            base_dir=Path.cwd(),
            tasks=[
                TaskSpec(id="b", command="true"),
                TaskSpec(id="a", command="true"),
                TaskSpec(id="c", command="true", needs=["a", "b"]),
            ],
        )

        plan = build_plan(pipeline)

        self.assertEqual([["a", "b"], ["c"]], [[task.id for task in level] for level in plan.levels])

    def test_downstream_tasks_walks_transitive_dependents(self):
        tasks = [
            TaskSpec(id="a", command="true"),
            TaskSpec(id="b", command="true", needs=["a"]),
            TaskSpec(id="c", command="true", needs=["b"]),
            TaskSpec(id="d", command="true"),
        ]

        self.assertEqual({"b", "c"}, downstream_tasks(tasks, {"a"}))


if __name__ == "__main__":
    unittest.main()
