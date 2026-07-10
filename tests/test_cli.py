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


def _env():
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    src = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    return env


if __name__ == "__main__":
    unittest.main()
