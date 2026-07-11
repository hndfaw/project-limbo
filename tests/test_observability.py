import json
import tempfile
import unittest
from pathlib import Path

from limbo.engine import LocalExecutor
from limbo.errors import ExecutionError
from limbo.observability import (
    EventLog,
    RunMetrics,
    build_timeline,
    looks_secret,
    redact_env,
)
from limbo.spec import load_pipeline


class RedactionTests(unittest.TestCase):
    def test_secret_names_detected(self):
        for name in ("API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "AWS_SECRET_ACCESS_KEY", "AUTH"):
            self.assertTrue(looks_secret(name, "whatever"), name)

    def test_plain_names_pass(self):
        for name in ("REGION", "PATH", "LOG_LEVEL", "PORT"):
            self.assertFalse(looks_secret(name, "value"), name)

    def test_secret_value_shapes_detected(self):
        self.assertTrue(looks_secret("HARMLESS", "sk-abc123"))
        self.assertTrue(looks_secret("X", "ghp_0123456789"))
        self.assertTrue(looks_secret("X", "-----BEGIN PRIVATE KEY-----"))
        self.assertFalse(looks_secret("X", "us-east-1"))

    def test_redact_env_replaces_only_secrets(self):
        redacted = redact_env({"API_KEY": "sk-secret", "REGION": "us", "TOKEN": "abc"})
        self.assertEqual("us", redacted["REGION"])
        self.assertNotIn("sk-secret", redacted.values())
        self.assertNotIn("abc", redacted.values())
        self.assertTrue(all(v == "***redacted***" for k, v in redacted.items() if k != "REGION"))


class EventLogTests(unittest.TestCase):
    def test_emit_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log = EventLog(Path(tmpdir) / "events.jsonl")
            log.emit("run_started", run_id="r1", task_count=2)
            log.emit("task_started", task_id="a", ignored=None)
            events = EventLog.read(Path(tmpdir) / "events.jsonl")
            self.assertEqual(["run_started", "task_started"], [e["type"] for e in events])
            self.assertEqual(2, events[0]["task_count"])
            self.assertNotIn("ignored", events[1])  # None fields are dropped
            self.assertTrue(all("ts" in e for e in events))

    def test_read_missing_file_returns_empty(self):
        self.assertEqual([], EventLog.read(Path("/nonexistent/events.jsonl")))


class RunMetricsTests(unittest.TestCase):
    class _Fake:
        def __init__(self, status, duration=0.0, queued_at=None, started_at=None):
            self.status = status
            self.duration_seconds = duration
            self.queued_at = queued_at
            self.started_at = started_at

    def test_from_results_counts_and_timings(self):
        metrics = RunMetrics.from_results([
            self._Fake("succeeded", duration=1.0, queued_at=0.0, started_at=0.5),
            self._Fake("failed", duration=2.0, queued_at=1.0, started_at=1.0),
            self._Fake("skipped", duration=0.0),
            self._Fake("blocked"),
        ])
        self.assertEqual(4, metrics.task_count)
        self.assertEqual(1, metrics.succeeded)
        self.assertEqual(1, metrics.failed)
        self.assertEqual(1, metrics.skipped)
        self.assertEqual(1, metrics.blocked)
        self.assertEqual(1, metrics.cache_hits)
        self.assertEqual(3.0, metrics.total_run_seconds)
        self.assertEqual(0.5, metrics.total_queue_seconds)

    def test_from_manifest(self):
        manifest = {"results": [
            {"status": "succeeded", "duration_seconds": 1.0},
            {"status": "skipped", "duration_seconds": 0.0},
        ]}
        metrics = RunMetrics.from_manifest(manifest)
        self.assertEqual(2, metrics.task_count)
        self.assertEqual(1, metrics.cache_hits)


class TimelineTests(unittest.TestCase):
    def test_build_timeline_orders_and_labels(self):
        events = [
            {"ts": 100.0, "type": "run_started", "task_count": 1},
            {"ts": 100.5, "type": "task_started", "task_id": "a"},
            {"ts": 100.2, "type": "task_queued", "task_id": "a"},
            {"ts": 101.0, "type": "run_finished", "status": "succeeded"},
        ]
        lines = build_timeline(events)
        self.assertIn("run started (1 task(s))", lines[0])
        self.assertIn("a: queued", lines[1])   # reordered by ts
        self.assertIn("a: started", lines[2])
        self.assertIn("run finished: succeeded", lines[3])

    def test_empty_events(self):
        self.assertEqual([], build_timeline([]))


class RunEventIntegrationTests(unittest.TestCase):
    def write_spec(self, base, payload):
        path = base / "limbo.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_pipeline(path)

    def events_for(self, base, run_id):
        return EventLog.read(base / ".limbo" / "runs" / run_id / "events.jsonl")

    def test_success_event_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {"version": 1, "tasks": [
                {"id": "a", "command": "true", "outputs": []},
            ]})
            result = LocalExecutor(base / ".limbo", max_workers=1).run(pipeline)
            types = [e["type"] for e in self.events_for(base, result.run_id)]
            self.assertEqual(
                ["run_started", "task_queued", "task_started", "task_succeeded", "run_finished"], types
            )

    def test_failure_and_blocked_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {"version": 1, "tasks": [
                {"id": "a", "command": "exit 1", "outputs": []},
                {"id": "b", "needs": ["a"], "command": "true", "outputs": []},
            ]})
            with self.assertRaises(ExecutionError):
                executor = LocalExecutor(base / ".limbo", max_workers=1)
                executor.run(pipeline)
            run_id = next((base / ".limbo" / "runs").iterdir()).name
            types = [e["type"] for e in self.events_for(base, run_id)]
            self.assertIn("task_failed", types)
            self.assertIn("task_blocked", types)
            self.assertEqual("run_finished", types[-1])

    def test_cache_hit_emits_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {"version": 1, "tasks": [
                {"id": "a", "command": "echo hi > a.txt", "outputs": ["a.txt"]},
            ]})
            executor = LocalExecutor(base / ".limbo", max_workers=1)
            executor.run(pipeline)
            second = executor.run(pipeline)
            types = [e["type"] for e in self.events_for(base, second.run_id)]
            self.assertIn("task_skipped", types)
            self.assertNotIn("task_started", types)

    def test_manifest_carries_metrics_and_secrets_are_redacted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {"version": 1, "tasks": [
                {"id": "a", "command": "true", "outputs": [],
                 "env": {"API_KEY": "sk-topsecret", "REGION": "us"}},
            ]})
            result = LocalExecutor(base / ".limbo", max_workers=1).run(pipeline)

            manifest = json.loads((base / ".limbo" / "runs" / result.run_id / "manifest.json").read_text())
            self.assertEqual(1, manifest["metrics"]["succeeded"])
            self.assertEqual(1, manifest["metrics"]["task_count"])

            events_text = (base / ".limbo" / "runs" / result.run_id / "events.jsonl").read_text()
            self.assertNotIn("sk-topsecret", events_text)
            self.assertIn("***redacted***", events_text)
            self.assertIn("us", events_text)  # non-secret env value preserved


if __name__ == "__main__":
    unittest.main()
