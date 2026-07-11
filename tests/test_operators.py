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

    def test_jsonl_expression_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_jsonl(base / "input.jsonl", [
                {"id": 1, "amount": 2}, {"id": 2, "amount": 8}, {"id": 3, "amount": 5},
            ])

            count = run_operator(
                {"type": "filter", "format": "jsonl", "input": "input.jsonl", "output": "big.jsonl",
                 "expr": "amount >= 5 and id != 3"}, base
            )

            self.assertEqual(1, count)
            self.assertEqual([{"id": 2, "amount": 8}], self.read_jsonl(base / "big.jsonl"))

    def test_csv_expression_filter_uses_conversion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_csv(base / "sales.csv", [
                {"team": "a", "amount": "2.5"}, {"team": "b", "amount": "9"},
            ])

            run_operator(
                {"type": "filter", "format": "csv", "input": "sales.csv", "output": "big.csv",
                 "expr": "float(amount) > 5"}, base
            )

            with (base / "big.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([{"team": "b", "amount": "9"}], rows)

    def test_jsonl_rename_preserves_values_and_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_jsonl(base / "input.jsonl", [{"first": "Ada", "last": "Love", "n": 3}])

            run_operator(
                {"type": "rename", "format": "jsonl", "input": "input.jsonl", "output": "out.jsonl",
                 "rename": {"first": "given", "last": "family"}}, base
            )

            self.assertEqual(
                [{"given": "Ada", "family": "Love", "n": 3}], self.read_jsonl(base / "out.jsonl")
            )

    def test_csv_rename_reorders_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_csv(base / "people.csv", [{"id": "1", "name": "Ada"}])

            run_operator(
                {"type": "rename", "format": "csv", "input": "people.csv", "output": "out.csv",
                 "rename": {"name": "full_name"}}, base
            )

            self.assertEqual("id,full_name\n1,Ada\n", (base / "out.csv").read_text(encoding="utf-8"))

    def test_rename_collision_with_existing_field_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_jsonl(base / "input.jsonl", [{"a": 1, "b": 2}])

            with self.assertRaisesRegex(OperatorError, "duplicate"):
                run_operator(
                    {"type": "rename", "format": "jsonl", "input": "input.jsonl", "output": "out.jsonl",
                     "rename": {"a": "b"}}, base
                )

    def test_csv_rename_collision_detected_on_empty_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "input.csv").write_text("a,b\n", encoding="utf-8")

            with self.assertRaisesRegex(OperatorError, "duplicate"):
                run_operator(
                    {"type": "rename", "format": "csv", "input": "input.csv", "output": "out.csv",
                     "rename": {"a": "b"}}, base
                )

    def test_jsonl_derive_adds_and_overwrites_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_jsonl(base / "input.jsonl", [
                {"first": "ada", "last": "love", "n": 3},
                {"first": "lin", "last": "tor", "n": 8},
            ])

            run_operator(
                {"type": "derive", "format": "jsonl", "input": "input.jsonl", "output": "out.jsonl",
                 "derived": {"full": "first + ' ' + last", "big": "n > 5", "n": "n * 2"}}, base
            )

            self.assertEqual(
                [{"first": "ada", "last": "love", "n": 6, "full": "ada love", "big": False},
                 {"first": "lin", "last": "tor", "n": 16, "full": "lin tor", "big": True}],
                self.read_jsonl(base / "out.jsonl"),
            )

    def test_csv_derive_appends_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_csv(base / "sales.csv", [{"item": "pen", "price": "2", "qty": "3"}])

            run_operator(
                {"type": "derive", "format": "csv", "input": "sales.csv", "output": "out.csv",
                 "derived": {"total": "float(price) * float(qty)"}}, base
            )

            with (base / "out.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
                handle.seek(0)
                header = handle.readline().strip()
            self.assertEqual("item,price,qty,total", header)
            self.assertEqual("6.0", rows[0]["total"])

    def test_derive_missing_field_reports_operator_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_jsonl(base / "input.jsonl", [{"a": 1}])

            with self.assertRaises(OperatorError):
                run_operator(
                    {"type": "derive", "format": "jsonl", "input": "input.jsonl", "output": "out.jsonl",
                     "derived": {"b": "ghost + 1"}}, base
                )
            self.assertFalse((base / "out.jsonl").exists())

    def test_derive_on_empty_input_writes_empty_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "input.jsonl").write_text("", encoding="utf-8")

            count = run_operator(
                {"type": "derive", "format": "jsonl", "input": "input.jsonl", "output": "out.jsonl",
                 "derived": {"x": "1 + 1"}}, base
            )

            self.assertEqual(0, count)
            self.assertEqual("", (base / "out.jsonl").read_text(encoding="utf-8"))

    def test_derive_deterministic_output_ordering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self.write_jsonl(base / "input.jsonl", [{"n": i} for i in range(5)])

            run_operator(
                {"type": "derive", "format": "jsonl", "input": "input.jsonl", "output": "out.jsonl",
                 "derived": {"squared": "n * n"}}, base
            )

            self.assertEqual(
                [i * i for i in range(5)],
                [row["squared"] for row in self.read_jsonl(base / "out.jsonl")],
            )

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
