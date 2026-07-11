import json
import tempfile
import unittest
from pathlib import Path

from limbo.engine import LocalExecutor
from limbo.errors import ExecutionError
from limbo.spec import load_pipeline


class EngineTests(unittest.TestCase):
    def write_spec(self, base, payload):
        path = base / "limbo.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_pipeline(path)

    def test_runs_tasks_and_skips_cache_on_second_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(
                base,
                {
                    "version": 1,
                    "tasks": [
                        {"id": "write", "command": "printf hello > out.txt", "outputs": ["out.txt"]},
                    ],
                },
            )
            executor = LocalExecutor(base / ".limbo", max_workers=1)

            first = executor.run(pipeline)
            second = executor.run(pipeline)

            self.assertEqual(["succeeded"], [result.status for result in first.results])
            self.assertEqual(["skipped"], [result.status for result in second.results])
            self.assertEqual("hello", (base / "out.txt").read_text(encoding="utf-8"))

    def test_input_change_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "input.txt").write_text("one", encoding="utf-8")
            pipeline = self.write_spec(
                base,
                {
                    "version": 1,
                    "tasks": [
                        {
                            "id": "copy",
                            "command": "cp input.txt output.txt",
                            "inputs": ["input.txt"],
                            "outputs": ["output.txt"],
                        }
                    ],
                },
            )
            executor = LocalExecutor(base / ".limbo", max_workers=1)

            executor.run(pipeline)
            (base / "input.txt").write_text("two", encoding="utf-8")
            result = executor.run(pipeline)

            self.assertEqual(["succeeded"], [item.status for item in result.results])
            self.assertEqual("two", (base / "output.txt").read_text(encoding="utf-8"))

    def test_failure_blocks_dependents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(
                base,
                {
                    "version": 1,
                    "tasks": [
                        {"id": "fail", "command": "exit 7"},
                        {"id": "after", "command": "printf bad > after.txt", "needs": ["fail"]},
                    ],
                },
            )
            executor = LocalExecutor(base / ".limbo", max_workers=1)

            with self.assertRaises(ExecutionError):
                executor.run(pipeline)

            manifest_paths = sorted((base / ".limbo" / "runs").glob("*/manifest.json"))
            manifest = json.loads(manifest_paths[-1].read_text(encoding="utf-8"))
            statuses = {item["task_id"]: item["status"] for item in manifest["results"]}
            self.assertEqual({"fail": "failed", "after": "blocked"}, statuses)
            self.assertFalse((base / "after.txt").exists())

    def test_dry_run_does_not_execute(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(
                base,
                {"version": 1, "tasks": [{"id": "write", "command": "printf hello > out.txt", "outputs": ["out.txt"]}]},
            )
            executor = LocalExecutor(base / ".limbo", max_workers=1)

            result = executor.run(pipeline, dry_run=True)

            self.assertEqual(["pending"], [item.status for item in result.results])
            self.assertFalse((base / "out.txt").exists())

    def test_timeout_fails_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(
                base,
                {
                    "version": 1,
                    "tasks": [
                        {
                            "id": "slow",
                            "command": "python -c \"import time; time.sleep(2)\"",
                            "timeout_seconds": 0.1,
                        }
                    ],
                },
            )
            executor = LocalExecutor(base / ".limbo", max_workers=1)

            with self.assertRaises(ExecutionError):
                executor.run(pipeline)

    def test_operator_executes_and_uses_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "input.jsonl").write_text('{"id": 1}\n', encoding="utf-8")
            pipeline = self.write_spec(base, {"version": 1, "tasks": [{
                "id": "project", "operator": {"type": "project", "format": "jsonl", "input": "input.jsonl",
                "output": "output.jsonl", "fields": ["id"]}
            }]})
            executor = LocalExecutor(base / ".limbo", max_workers=1)

            first = executor.run(pipeline)
            second = executor.run(pipeline)

            self.assertEqual("succeeded", first.results[0].status)
            self.assertEqual("skipped", second.results[0].status)
            self.assertEqual('{"id": 1}\n', (base / "output.jsonl").read_text(encoding="utf-8"))

    def test_operator_data_error_is_a_task_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "input.jsonl").write_text("invalid\n", encoding="utf-8")
            pipeline = self.write_spec(base, {"version": 1, "tasks": [{
                "id": "project", "operator": {"type": "project", "format": "jsonl", "input": "input.jsonl",
                "output": "output.jsonl", "fields": ["id"]}
            }]})

            with self.assertRaisesRegex(ExecutionError, "project"):
                LocalExecutor(base / ".limbo", max_workers=1).run(pipeline)

            stderr = next((base / ".limbo" / "runs").glob("*/project/stderr.log"))
            self.assertIn("could not read", stderr.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
