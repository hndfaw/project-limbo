import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_validate_and_run_cli(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()
            spec = base / "limbo.json"
            spec.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "tasks": [
                            {"id": "write", "command": "printf ok > out.txt", "outputs": ["out.txt"]},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            validate = subprocess.run(
                [sys.executable, "-m", "limbo.cli", "validate", str(spec)],
                cwd=str(base),
                env=_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            run = subprocess.run(
                [sys.executable, "-m", "limbo.cli", "run", str(spec), "--state-dir", str(base / ".limbo")],
                cwd=str(base),
                env=_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, validate.returncode, validate.stderr)
            self.assertEqual(0, run.returncode, run.stderr)
            self.assertEqual("ok", (base / "out.txt").read_text(encoding="utf-8"))

    def test_cli_reports_spec_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()
            spec = base / "bad.json"
            spec.write_text('{"version": 1, "tasks": []}', encoding="utf-8")

            result = subprocess.run(
                [sys.executable, "-m", "limbo.cli", "validate", str(spec)],
                cwd=str(base),
                env=_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(1, result.returncode)
            self.assertIn("error:", result.stderr)


    def test_cli_resume_after_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()
            state = base / ".limbo"
            spec = base / "limbo.json"
            spec.write_text(
                json.dumps({"version": 1, "tasks": [
                    {"id": "gate", "command": "test -f gate.txt && echo ok > gate_done.txt",
                     "outputs": ["gate_done.txt"]},
                ]}),
                encoding="utf-8",
            )

            first = subprocess.run(
                [sys.executable, "-m", "limbo.cli", "run", str(spec), "--state-dir", str(state)],
                cwd=str(base), env=_env(), text=True, capture_output=True, check=False,
            )
            self.assertEqual(2, first.returncode)
            self.assertIn("failure summary:", first.stderr)

            run_id = next((state / "runs").iterdir()).name
            (base / "gate.txt").write_text("go", encoding="utf-8")

            resumed = subprocess.run(
                [sys.executable, "-m", "limbo.cli", "resume", run_id, "--state-dir", str(state)],
                cwd=str(base), env=_env(), text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, resumed.returncode, resumed.stderr)
            self.assertIn(f"resumed from {run_id}", resumed.stdout)
            self.assertTrue((base / "gate_done.txt").exists())


    def test_cli_version(self):
        result = subprocess.run(
            [sys.executable, "-m", "limbo.cli", "--version"],
            env=_env(), text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("limbo", result.stdout)

    def test_cli_runs_lists_prior_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()
            state = base / ".limbo"
            spec = base / "limbo.json"
            spec.write_text(
                json.dumps({"version": 1, "tasks": [
                    {"id": "write", "command": "printf ok > out.txt", "outputs": ["out.txt"]},
                ]}),
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, "-m", "limbo.cli", "run", str(spec), "--state-dir", str(state)],
                cwd=str(base), env=_env(), text=True, capture_output=True, check=False,
            )

            runs = subprocess.run(
                [sys.executable, "-m", "limbo.cli", "runs", "--state-dir", str(state)],
                cwd=str(base), env=_env(), text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, runs.returncode, runs.stderr)
            self.assertIn("1 succeeded", runs.stdout)


    def test_cli_inspect_and_timeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()
            state = base / ".limbo"
            spec = base / "limbo.json"
            spec.write_text(
                json.dumps({"version": 1, "tasks": [
                    {"id": "write", "command": "printf ok > out.txt", "outputs": ["out.txt"]},
                ]}),
                encoding="utf-8",
            )
            subprocess.run(
                [sys.executable, "-m", "limbo.cli", "run", str(spec), "--state-dir", str(state)],
                cwd=str(base), env=_env(), text=True, capture_output=True, check=False,
            )
            run_id = next((state / "runs").iterdir()).name

            inspect = subprocess.run(
                [sys.executable, "-m", "limbo.cli", "inspect", run_id, "--state-dir", str(state)],
                cwd=str(base), env=_env(), text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, inspect.returncode, inspect.stderr)
            self.assertIn("write: succeeded", inspect.stdout)
            self.assertIn("metrics:", inspect.stdout)

            timeline = subprocess.run(
                [sys.executable, "-m", "limbo.cli", "timeline", run_id, "--state-dir", str(state)],
                cwd=str(base), env=_env(), text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, timeline.returncode, timeline.stderr)
            self.assertIn("run started", timeline.stdout)
            self.assertIn("write: succeeded", timeline.stdout)
            self.assertIn("run finished: succeeded", timeline.stdout)

    def test_cli_inspect_unknown_run_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()
            result = subprocess.run(
                [sys.executable, "-m", "limbo.cli", "inspect", "nope", "--state-dir", str(base / ".limbo")],
                cwd=str(base), env=_env(), text=True, capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("no run found", result.stderr)


def _env():
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    src = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    return env


if __name__ == "__main__":
    unittest.main()
