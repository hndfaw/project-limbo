import tempfile
import unittest
from pathlib import Path

from limbo.fingerprint import outputs_exist, task_fingerprint
from limbo.spec import TaskSpec


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_changes_when_input_content_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            data = base / "data.txt"
            data.write_text("one", encoding="utf-8")
            task = TaskSpec(id="a", command="cat data.txt", inputs=["data.txt"])

            first = task_fingerprint(task, base)
            data.write_text("two", encoding="utf-8")
            second = task_fingerprint(task, base)

            self.assertNotEqual(first, second)

    def test_fingerprint_is_stable_for_same_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "data.txt").write_text("one", encoding="utf-8")
            task = TaskSpec(id="a", command="cat data.txt", inputs=["data.txt"], env={"X": "1"})

            self.assertEqual(task_fingerprint(task, base), task_fingerprint(task, base))

    def test_outputs_exist_requires_declared_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            task = TaskSpec(id="a", command="true", outputs=["out.txt"])

            self.assertFalse(outputs_exist(task, base))
            (base / "out.txt").write_text("done", encoding="utf-8")
            self.assertTrue(outputs_exist(task, base))


if __name__ == "__main__":
    unittest.main()
