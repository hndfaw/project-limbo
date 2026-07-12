"""End-to-end verification that drives the real `limbo` CLI through a full run.

These exercise the installed command surface (validate/run/runs/inspect/timeline/
resume) against realistic pipelines: an operator stage, a retry that recovers, a
cache hit on re-run, and a failure that blocks a dependent then resumes. They
complement the unit tests by confirming the CLI behaves end-to-end via subprocess.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _env():
    env = os.environ.copy()
    src = str(REPO_ROOT / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    return env


def _cli(base, *args):
    return subprocess.run(
        [sys.executable, "-m", "limbo.cli", *args],
        cwd=str(base), env=_env(), text=True, capture_output=True, check=False,
    )


def _run_id(stdout):
    # `run`/`resume` print "run <run-id>" (optionally "(resumed from ...)") first.
    return stdout.splitlines()[0].split()[1]


def _latest_run_id(base, state):
    # On failure the CLI prints to stderr and raises before printing the run id,
    # but the manifest is still written — so read it back via `runs`.
    runs = _cli(base, "runs", "--state-dir", state, "--json")
    return json.loads(runs.stdout)["runs"][0]["run_id"]


class EndToEndSuccessTests(unittest.TestCase):
    def _write(self, base, name, content):
        (base / name).write_text(content, encoding="utf-8")

    def test_operator_retry_cache_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            state = str(base / ".limbo")
            self._write(base, "data.jsonl", '{"id": 1, "active": true}\n{"id": 2, "active": false}\n')
            spec = {
                "version": 1,
                "tasks": [
                    {"id": "filter", "operator": {
                        "type": "filter", "format": "jsonl",
                        "input": "data.jsonl", "output": "active.jsonl", "expr": "active"}},
                    {"id": "flaky", "needs": ["filter"],
                     "command": "n=$(cat n.txt 2>/dev/null || echo 0); n=$((n+1)); echo $n > n.txt; test $n -ge 2",
                     "outputs": ["n.txt"], "retry": {"max_attempts": 3, "delay_seconds": 0}},
                    {"id": "final", "needs": ["flaky"],
                     "command": "wc -l < active.jsonl > count.txt",
                     "inputs": ["active.jsonl"], "outputs": ["count.txt"]},
                ],
            }
            self._write(base, "limbo.json", json.dumps(spec))

            self.assertEqual(0, _cli(base, "validate", "limbo.json").returncode)

            run = _cli(base, "run", "limbo.json", "--state-dir", state)
            self.assertEqual(0, run.returncode, run.stderr)
            for task in ("filter", "flaky", "final"):
                self.assertIn(f"{task}: succeeded", run.stdout)
            run_id = _run_id(run.stdout)

            # Operator kept only the active row; the flaky task recovered via retry.
            self.assertEqual([{"active": True, "id": 1}],
                             [json.loads(line) for line in (base / "active.jsonl").read_text().splitlines()])

            # Second run is a full cache hit.
            rerun = _cli(base, "run", "limbo.json", "--state-dir", state)
            self.assertEqual(0, rerun.returncode, rerun.stderr)
            self.assertEqual(3, rerun.stdout.count("skipped"))

            inspect = _cli(base, "inspect", run_id, "--state-dir", state)
            self.assertEqual(0, inspect.returncode, inspect.stderr)
            self.assertIn("succeeded=3", inspect.stdout)

            timeline = _cli(base, "timeline", run_id, "--state-dir", state)
            self.assertEqual(0, timeline.returncode, timeline.stderr)
            self.assertIn("run started", timeline.stdout)
            self.assertIn("flaky: succeeded", timeline.stdout)

            runs = _cli(base, "runs", "--state-dir", state)
            self.assertEqual(0, runs.returncode, runs.stderr)
            self.assertIn(run_id, runs.stdout)


class EndToEndFailureResumeTests(unittest.TestCase):
    def _write(self, base, name, content):
        (base / name).write_text(content, encoding="utf-8")

    def test_failure_blocks_dependent_then_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            state = str(base / ".limbo")
            spec = {
                "version": 1,
                "tasks": [
                    {"id": "a", "command": "echo a > a.txt", "outputs": ["a.txt"]},
                    {"id": "gate", "needs": ["a"],
                     "command": "test -f go.txt && echo done > gate.txt", "outputs": ["gate.txt"]},
                    {"id": "c", "needs": ["gate"], "command": "echo c > c.txt", "outputs": ["c.txt"]},
                ],
            }
            self._write(base, "limbo.json", json.dumps(spec))

            run = _cli(base, "run", "limbo.json", "--state-dir", state)
            self.assertEqual(2, run.returncode)  # gate fails, c is blocked
            self.assertIn("failure summary", run.stderr)
            self.assertIn("gate:", run.stderr)
            run_id = _latest_run_id(base, state)
            self.assertFalse((base / "c.txt").exists())

            # Satisfy the gate, then resume: `a` is carried forward, gate + c run.
            self._write(base, "go.txt", "go")
            resume = _cli(base, "resume", run_id, "--state-dir", state)
            self.assertEqual(0, resume.returncode, resume.stderr)
            self.assertIn("a: skipped", resume.stdout)
            self.assertIn("gate: succeeded", resume.stdout)
            self.assertIn("c: succeeded", resume.stdout)
            self.assertTrue((base / "c.txt").exists())


if __name__ == "__main__":
    unittest.main()
