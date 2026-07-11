import csv
import json
import tempfile
import unittest
from pathlib import Path

from limbo.operators import OperatorError, run_operator


class OperatorTests(unittest.TestCase):
    def test_jsonl_filter_and_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_jsonl(base / "input.jsonl", [
                {"id": 1, "active": True, "secret": "a"},
                {"id": 2, "active": False, "secret": "b"},
            ])

            count = run_operator(
                {"type": "filter", "format": "jsonl", "input": "input.jsonl", "output": "active.jsonl",
                 "where": {"field": "active", "equals": True}}, base
            )
            run_operator(
                {"type": "project", "format": "jsonl", "input": "active.jsonl", "output": "result.jsonl",
                 "fields": ["id", "missing"]}, base
            )

            self.assertEqual(1, count)
            self.assertEqual([{"id": 1, "missing": None}], self.read_jsonl(base / "result.jsonl"))

    def test_csv_left_join_disambiguates_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_csv(base / "people.csv", [
                {"id": "1", "name": "Ada"}, {"id": "2", "name": "Lin"}
            ])
            self.write_csv(base / "teams.csv", [{"id": "1", "name": "Core", "role": "dev"}])

            run_operator(
                {"type": "join", "format": "csv", "left": "people.csv", "right": "teams.csv",
                 "output": "joined.csv", "on": "id", "how": "left"}, base
            )

            with (base / "joined.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(
                [{"id": "1", "name": "Ada", "name_right": "Core", "role": "dev"},
                 {"id": "2", "name": "Lin", "name_right": "", "role": ""}], rows
            )

    def test_csv_grouped_aggregations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_csv(base / "sales.csv", [
                {"team": "a", "amount": "2.5"}, {"team": "a", "amount": "3.5"},
                {"team": "b", "amount": "4"},
            ])

            run_operator(
                {"type": "aggregate", "format": "csv", "input": "sales.csv", "output": "totals.csv",
                 "group_by": ["team"], "aggregations": {
                     "count": {"op": "count"}, "total": {"op": "sum", "field": "amount"},
                     "average": {"op": "avg", "field": "amount"},
                     "low": {"op": "min", "field": "amount"}, "high": {"op": "max", "field": "amount"},
                 }}, base
            )

            with (base / "totals.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual("6", rows[0]["total"])
            self.assertEqual("3", rows[0]["average"])
            self.assertEqual("1", rows[1]["count"])

    def test_invalid_jsonl_reports_file_and_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "bad.jsonl").write_text('{"ok": true}\nnot-json\n', encoding="utf-8")

            with self.assertRaisesRegex(OperatorError, "bad.jsonl"):
                run_operator(
                    {"type": "project", "format": "jsonl", "input": "bad.jsonl", "output": "out.jsonl",
                     "fields": ["ok"]}, base
                )

            self.assertFalse((base / "out.jsonl").exists())

    def test_empty_csv_result_keeps_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_csv(base / "input.csv", [{"id": "1", "active": "yes"}])

            run_operator(
                {"type": "filter", "format": "csv", "input": "input.csv", "output": "empty.csv",
                 "where": {"field": "active", "equals": "no"}}, base
            )

            self.assertEqual("id,active\n", (base / "empty.csv").read_text(encoding="utf-8"))

    @staticmethod
    def write_jsonl(path, rows):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    @staticmethod
    def read_jsonl(path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    @staticmethod
    def write_csv(path, rows):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
