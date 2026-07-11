import json
import tempfile
import unittest
from pathlib import Path

from limbo.errors import SpecError
from limbo.spec import load_pipeline


class SpecTests(unittest.TestCase):
    def write_spec(self, tmpdir, payload):
        path = Path(tmpdir) / "limbo.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_loads_valid_pipeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(
                tmpdir,
                {
                    "version": 1,
                    "tasks": [
                        {"id": "a", "command": "true"},
                        {"id": "b", "command": "true", "needs": ["a"]},
                    ],
                },
            )

            pipeline = load_pipeline(path)

            self.assertEqual(["a", "b"], [task.id for task in pipeline.tasks])
            self.assertEqual(Path(tmpdir).resolve(), pipeline.base_dir)

    def test_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(
                tmpdir,
                {
                    "version": 1,
                    "tasks": [
                        {"id": "a", "command": "true"},
                        {"id": "a", "command": "true"},
                    ],
                },
            )

            with self.assertRaisesRegex(SpecError, "duplicate"):
                load_pipeline(path)

    def test_rejects_missing_dependency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(
                tmpdir,
                {
                    "version": 1,
                    "tasks": [{"id": "a", "command": "true", "needs": ["missing"]}],
                },
            )

            with self.assertRaisesRegex(SpecError, "missing dependency"):
                load_pipeline(path)

    def test_rejects_cycles(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(
                tmpdir,
                {
                    "version": 1,
                    "tasks": [
                        {"id": "a", "command": "true", "needs": ["b"]},
                        {"id": "b", "command": "true", "needs": ["a"]},
                    ],
                },
            )

            with self.assertRaisesRegex(SpecError, "cycle"):
                load_pipeline(path)

    def test_rejects_bad_timeout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(
                tmpdir,
                {"version": 1, "tasks": [{"id": "a", "command": "true", "timeout_seconds": 0}]},
            )

            with self.assertRaisesRegex(SpecError, "timeout_seconds"):
                load_pipeline(path)

    def test_loads_operator_and_derives_cache_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(tmpdir, {"version": 1, "tasks": [{
                "id": "filter", "operator": {"type": "filter", "format": "jsonl", "input": "in.jsonl",
                "output": "out.jsonl", "where": {"field": "active", "equals": True}}
            }]})

            task = load_pipeline(path).tasks[0]

            self.assertIsNone(task.command)
            self.assertEqual(["in.jsonl"], task.inputs)
            self.assertEqual(["out.jsonl"], task.outputs)

    def test_rejects_command_and_operator_together(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(tmpdir, {"version": 1, "tasks": [{
                "id": "bad", "command": "true", "operator": {"type": "project", "format": "csv",
                "input": "in.csv", "output": "out.csv", "fields": ["id"]}
            }]})
            with self.assertRaisesRegex(SpecError, "exactly one"):
                load_pipeline(path)

    def test_rejects_invalid_operator_configuration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(tmpdir, {"version": 1, "tasks": [{
                "id": "bad", "operator": {"type": "join", "format": "csv", "left": "a.csv",
                "right": "b.csv", "output": "out.csv"}
            }]})
            with self.assertRaisesRegex(SpecError, "requires non-empty 'on'"):
                load_pipeline(path)

    def test_loads_derive_operator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(tmpdir, {"version": 1, "tasks": [{
                "id": "derive", "operator": {"type": "derive", "format": "jsonl", "input": "in.jsonl",
                "output": "out.jsonl", "derived": {"total": "price * qty"}}
            }]})

            task = load_pipeline(path).tasks[0]

            self.assertEqual("derive", task.operator["type"])
            self.assertEqual(["in.jsonl"], task.inputs)
            self.assertEqual(["out.jsonl"], task.outputs)

    def test_rejects_filter_with_both_where_and_expr(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(tmpdir, {"version": 1, "tasks": [{
                "id": "bad", "operator": {"type": "filter", "format": "jsonl", "input": "in.jsonl",
                "output": "out.jsonl", "where": {"field": "a", "equals": 1}, "expr": "a == 1"}
            }]})
            with self.assertRaisesRegex(SpecError, "exactly one of 'where' or 'expr'"):
                load_pipeline(path)

    def test_rejects_invalid_expression_at_load_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(tmpdir, {"version": 1, "tasks": [{
                "id": "bad", "operator": {"type": "derive", "format": "jsonl", "input": "in.jsonl",
                "output": "out.jsonl", "derived": {"x": "__import__('os')"}}
            }]})
            with self.assertRaisesRegex(SpecError, "unknown function"):
                load_pipeline(path)

    def test_loads_retry_policy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(tmpdir, {"version": 1, "tasks": [{
                "id": "t", "command": "true",
                "retry": {"max_attempts": 3, "backoff": "exponential", "delay_seconds": 1}
            }]})

            task = load_pipeline(path).tasks[0]

            self.assertEqual(3, task.retry.max_attempts)
            self.assertEqual("exponential", task.retry.backoff)

    def test_default_retry_is_single_attempt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(tmpdir, {"version": 1, "tasks": [{
                "id": "t", "command": "true"
            }]})

            self.assertEqual(1, load_pipeline(path).tasks[0].retry.max_attempts)

    def test_rejects_bad_retry_policy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(tmpdir, {"version": 1, "tasks": [{
                "id": "t", "command": "true", "retry": {"max_attempts": 0}
            }]})
            with self.assertRaisesRegex(SpecError, "max_attempts"):
                load_pipeline(path)

    def test_rejects_rename_target_collision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(tmpdir, {"version": 1, "tasks": [{
                "id": "bad", "operator": {"type": "rename", "format": "jsonl", "input": "in.jsonl",
                "output": "out.jsonl", "rename": {"a": "x", "b": "x"}}
            }]})
            with self.assertRaisesRegex(SpecError, "same name"):
                load_pipeline(path)


if __name__ == "__main__":
    unittest.main()
