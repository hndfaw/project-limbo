import contextlib
import io
import re
import unittest
from pathlib import Path

import limbo
from limbo.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def _pyproject_version(self) -> str:
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        self.assertIsNotNone(match, "version not found in pyproject.toml")
        assert match is not None
        return match.group(1)

    def test_version_matches_pyproject(self):
        self.assertEqual(self._pyproject_version(), limbo.__version__)

    def test_console_entry_point_target_is_callable(self):
        # pyproject declares  limbo = "limbo.cli:main"  as the console script.
        self.assertTrue(callable(main))
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('limbo = "limbo.cli:main"', text)

    def test_version_flag_exits_zero(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(out):
            main(["--version"])
        self.assertEqual(0, ctx.exception.code)
        self.assertIn(limbo.__version__, out.getvalue())

    def test_public_api_is_importable(self):
        for name in limbo.__all__:
            self.assertTrue(hasattr(limbo, name), name)


if __name__ == "__main__":
    unittest.main()
