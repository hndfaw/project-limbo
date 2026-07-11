import json
import tempfile
import unittest
from pathlib import Path

from limbo.artifacts import ArtifactStore
from limbo.engine import LocalExecutor
from limbo.errors import ExecutionError
from limbo.spec import load_pipeline


class EngineTests(unittest.TestCase):
    def write_spec(self, base, payload):
        path = base / "limbo.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_pipeline(path)

    def test_artifact_store_records_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {"version": 1, "tasks": [
                {"id": "gen", "command": "printf hello > out.txt", "outputs": ["out.txt"]},
            ]})
            store = ArtifactStore(base / ".limbo" / "artifacts")
            executor = LocalExecutor(base / ".limbo", max_workers=1, artifact_store=store)

            result = executor.run(pipeline)

            artifacts = result.results[0].artifacts
            self.assertEqual(1, len(artifacts))
            self.assertEqual("gen", artifacts[0].producer)
            self.assertEqual("out.txt", artifacts[0].logical_path)
            self.assertEqual(5, artifacts[0].size)
            self.assertTrue(store.exists(artifacts[0].digest))
            manifest = json.loads((base / ".limbo" / "runs" / result.run_id / "manifest.json").read_text())
            self.assertEqual(1, len(manifest["results"][0]["artifacts"]))

    def test_digest_cache_validation_detects_changed_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {"version": 1, "tasks": [
                {"id": "gen", "command": "printf hello > out.txt", "outputs": ["out.txt"]},
            ]})
            store = ArtifactStore(base / ".limbo" / "artifacts")
            executor = LocalExecutor(base / ".limbo", max_workers=1, artifact_store=store)

            first = executor.run(pipeline)
            second = executor.run(pipeline)  # unchanged -> cache hit
            (base / "out.txt").write_text("tampered", encoding="utf-8")
            third = executor.run(pipeline)   # output digest changed -> must re-run

            self.assertEqual("succeeded", first.results[0].status)
            self.assertEqual("skipped", second.results[0].status)
            self.assertEqual("succeeded", third.results[0].status)

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

    def test_end_to_end_operator_pipeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "sales.jsonl").write_text(
                '{"team": "a", "amount": 2, "active": true}\n'
                '{"team": "a", "amount": 8, "active": true}\n'
                '{"team": "b", "amount": 5, "active": false}\n'
                '{"team": "b", "amount": 9, "active": true}\n',
                encoding="utf-8",
            )
            pipeline = self.write_spec(base, {"version": 1, "tasks": [
                {"id": "active", "operator": {"type": "filter", "format": "jsonl", "input": "sales.jsonl",
                    "output": "active.jsonl", "expr": "active and amount >= 5"}},
                {"id": "labelled", "needs": ["active"], "operator": {"type": "derive", "format": "jsonl",
                    "input": "active.jsonl", "output": "labelled.jsonl",
                    "derived": {"tier": "'high' if amount >= 8 else 'mid'"}}},
                {"id": "renamed", "needs": ["labelled"], "operator": {"type": "rename", "format": "jsonl",
                    "input": "labelled.jsonl", "output": "renamed.jsonl", "rename": {"amount": "value"}}},
                {"id": "rollup", "needs": ["renamed"], "operator": {"type": "aggregate", "format": "jsonl",
                    "input": "renamed.jsonl", "output": "rollup.jsonl", "group_by": ["team"],
                    "aggregations": {"count": {"op": "count"}, "total": {"op": "sum", "field": "value"}}}},
            ]})

            result = LocalExecutor(base / ".limbo", max_workers=2).run(pipeline)

            self.assertEqual(4, len(result.succeeded))
            rollup = [json.loads(line) for line in (base / "rollup.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [{"team": "a", "count": 1, "total": 8}, {"team": "b", "count": 1, "total": 9}],
                sorted(rollup, key=lambda row: row["team"]),
            )

    def test_retry_recovers_from_transient_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            # Fails on the first two attempts, succeeds on the third.
            command = ("n=$(cat n.txt 2>/dev/null || echo 0); n=$((n+1)); "
                       "echo $n > n.txt; test $n -ge 3")
            pipeline = self.write_spec(base, {"version": 1, "tasks": [{
                "id": "flaky", "command": command, "outputs": ["n.txt"],
                "retry": {"max_attempts": 3, "delay_seconds": 0}
            }]})

            result = LocalExecutor(base / ".limbo", max_workers=1).run(pipeline)

            flaky = result.results[0]
            self.assertEqual("succeeded", flaky.status)
            self.assertEqual(3, len(flaky.attempts))
            self.assertEqual(["failed", "failed", "succeeded"], [a.status for a in flaky.attempts])

    def test_non_retryable_exit_code_fails_immediately(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {"version": 1, "tasks": [{
                "id": "boom", "command": "exit 1", "outputs": [],
                # Only exit code 2 is retryable, so exit 1 fails on the first attempt.
                "retry": {"max_attempts": 5, "delay_seconds": 0, "retry_on_exit_codes": [2]}
            }]})

            with self.assertRaises(ExecutionError):
                LocalExecutor(base / ".limbo", max_workers=1).run(pipeline)

            manifest = json.loads((next((base / ".limbo" / "runs").glob("*/manifest.json"))).read_text())
            boom = next(r for r in manifest["results"] if r["task_id"] == "boom")
            self.assertEqual("failed", boom["status"])
            self.assertEqual(1, len(boom["attempts"]))

    def test_manifest_records_pipeline_and_attempts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {"version": 1, "tasks": [
                {"id": "ok", "command": "echo hi > ok.txt", "outputs": ["ok.txt"]},
            ]})

            result = LocalExecutor(base / ".limbo", max_workers=1).run(pipeline)

            manifest = json.loads((base / ".limbo" / "runs" / result.run_id / "manifest.json").read_text())
            self.assertEqual(str((base / "limbo.json").resolve()), manifest["pipeline"])
            self.assertIsNone(manifest["resumed_from"])
            self.assertEqual(1, len(manifest["results"][0]["attempts"]))

    def test_resume_carries_success_and_reruns_failed_and_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            # b fails until gate.txt exists; c depends on b and is blocked on the first run.
            pipeline = self.write_spec(base, {"version": 1, "tasks": [
                {"id": "a", "command": "echo a > a.txt", "outputs": ["a.txt"]},
                {"id": "b", "needs": ["a"], "command": "test -f gate.txt && echo b > b.txt",
                 "outputs": ["b.txt"]},
                {"id": "c", "needs": ["b"], "command": "echo c > c.txt", "outputs": ["c.txt"]},
            ]})
            executor = LocalExecutor(base / ".limbo", max_workers=1)

            with self.assertRaises(ExecutionError):
                executor.run(pipeline)
            run_id = next((base / ".limbo" / "runs").iterdir()).name
            self.assertTrue((base / "a.txt").exists())
            self.assertFalse((base / "c.txt").exists())

            # Satisfy the gate, then resume: a is carried (cache), b reruns, c runs.
            (base / "gate.txt").write_text("go", encoding="utf-8")
            resumed = executor.resume(run_id)

            statuses = {r.task_id: r.status for r in resumed.results}
            self.assertEqual("skipped", statuses["a"])
            self.assertEqual("succeeded", statuses["b"])
            self.assertEqual("succeeded", statuses["c"])
            self.assertEqual(run_id, resumed.resumed_from)
            self.assertTrue((base / "c.txt").exists())

    def test_list_runs_summarizes_newest_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {"version": 1, "tasks": [
                {"id": "ok", "command": "echo hi > ok.txt", "outputs": ["ok.txt"]},
            ]})
            executor = LocalExecutor(base / ".limbo", max_workers=1)

            first = executor.run(pipeline)
            second = executor.run(pipeline, force=True)

            runs = executor.list_runs()
            self.assertEqual([second.run_id, first.run_id], [r["run_id"] for r in runs])
            self.assertEqual({"succeeded": 1}, runs[0]["counts"])
            self.assertEqual(str((base / "limbo.json").resolve()), runs[0]["pipeline"])
            self.assertEqual(1, len(executor.list_runs(limit=1)))

    def test_list_runs_empty_when_no_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual([], LocalExecutor(Path(tmpdir) / ".limbo").list_runs())

    def test_resume_unknown_run_id_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            with self.assertRaisesRegex(ExecutionError, "no run found"):
                LocalExecutor(base / ".limbo", max_workers=1).resume("does-not-exist")

    def test_failure_summary_includes_attempts_and_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {"version": 1, "tasks": [
                {"id": "b", "command": "exit 1", "outputs": [],
                 "retry": {"max_attempts": 2, "delay_seconds": 0}},
                {"id": "c", "needs": ["b"], "command": "echo c", "outputs": []},
            ]})

            try:
                LocalExecutor(base / ".limbo", max_workers=1).run(pipeline)
                self.fail("expected ExecutionError")
            except ExecutionError as exc:
                summary = exc.run_result.failure_summary()

            self.assertIn("b: exit code 1 after 2 attempt(s)", summary)
            self.assertIn("attempt 1: failed", summary)
            self.assertIn("c: blocked", summary)

    def test_expression_evaluation_error_is_a_task_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "input.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
            pipeline = self.write_spec(base, {"version": 1, "tasks": [{
                "id": "derive", "operator": {"type": "derive", "format": "jsonl", "input": "input.jsonl",
                "output": "output.jsonl", "derived": {"b": "ghost + 1"}}
            }]})

            with self.assertRaisesRegex(ExecutionError, "derive"):
                LocalExecutor(base / ".limbo", max_workers=1).run(pipeline)

            stderr = next((base / ".limbo" / "runs").glob("*/derive/stderr.log"))
            self.assertIn("ghost", stderr.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
