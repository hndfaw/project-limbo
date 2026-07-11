import unittest

from limbo.expressions import ExpressionError, compile_expression


class ExpressionTests(unittest.TestCase):
    def evaluate(self, source, row=None):
        return compile_expression(source).evaluate(row or {})

    def test_arithmetic_and_precedence(self):
        self.assertEqual(14, self.evaluate("2 + 3 * 4"))
        self.assertEqual(20, self.evaluate("(2 + 3) * 4"))
        self.assertEqual(2, self.evaluate("7 // 3"))
        self.assertEqual(1, self.evaluate("7 % 3"))
        self.assertEqual(8, self.evaluate("2 ** 3"))

    def test_field_references(self):
        self.assertEqual(5, self.evaluate("a + b", {"a": 2, "b": 3}))
        self.assertTrue(self.evaluate("active", {"active": True}))

    def test_comparisons_and_boolean_logic(self):
        self.assertTrue(self.evaluate("a > 3 and a < 10", {"a": 5}))
        self.assertFalse(self.evaluate("a > 3 and a < 10", {"a": 2}))
        self.assertTrue(self.evaluate("a == 1 or a == 2", {"a": 2}))
        self.assertTrue(self.evaluate("not missing", {"missing": False}))

    def test_chained_comparison(self):
        self.assertTrue(self.evaluate("1 < a < 10", {"a": 5}))
        self.assertFalse(self.evaluate("1 < a < 10", {"a": 20}))

    def test_membership(self):
        self.assertTrue(self.evaluate("role in roles", {"role": "dev", "roles": ["dev", "ops"]}))
        self.assertTrue(self.evaluate("role not in roles", {"role": "qa", "roles": ["dev", "ops"]}))
        self.assertTrue(self.evaluate("team in ['a', 'b']", {"team": "b"}))

    def test_conditional_expression(self):
        self.assertEqual("big", self.evaluate("'big' if n > 5 else 'small'", {"n": 9}))
        self.assertEqual("small", self.evaluate("'big' if n > 5 else 'small'", {"n": 1}))

    def test_allowed_functions(self):
        self.assertEqual("ada", self.evaluate("lower(name)", {"name": "ADA"}))
        self.assertEqual(3, self.evaluate("len(name)", {"name": "ada"}))
        self.assertEqual(3.5, self.evaluate("float(amount)", {"amount": "3.5"}))
        self.assertEqual(4, self.evaluate("round(x)", {"x": 3.6}))
        self.assertTrue(self.evaluate("startswith(name, 'a')", {"name": "ada"}))
        self.assertEqual("fallback", self.evaluate("coalesce(a, b, 'fallback')", {"a": None, "b": None}))

    def test_missing_field_raises(self):
        with self.assertRaisesRegex(ExpressionError, "absent|not present"):
            self.evaluate("ghost + 1", {"present": 1})

    def test_division_by_zero_raises_expression_error(self):
        with self.assertRaises(ExpressionError):
            self.evaluate("a / 0", {"a": 1})

    def test_type_error_is_wrapped(self):
        with self.assertRaises(ExpressionError):
            self.evaluate("a + b", {"a": 1, "b": "x"})

    def test_unknown_function_rejected(self):
        with self.assertRaisesRegex(ExpressionError, "unknown function"):
            compile_expression("eval('1')")

    def test_attribute_access_rejected(self):
        with self.assertRaises(ExpressionError):
            compile_expression("value.__class__")

    def test_subscript_rejected(self):
        with self.assertRaises(ExpressionError):
            compile_expression("value[0]")

    def test_dunder_import_rejected(self):
        with self.assertRaises(ExpressionError):
            compile_expression("__import__('os')")

    def test_lambda_and_comprehension_rejected(self):
        with self.assertRaises(ExpressionError):
            compile_expression("[x for x in items]")
        with self.assertRaises(ExpressionError):
            compile_expression("lambda: 1")

    def test_keyword_arguments_rejected(self):
        with self.assertRaises(ExpressionError):
            compile_expression("round(x, ndigits=2)")

    def test_empty_expression_rejected(self):
        with self.assertRaises(ExpressionError):
            compile_expression("   ")

    def test_syntax_error_reported(self):
        with self.assertRaisesRegex(ExpressionError, "invalid expression"):
            compile_expression("a +")


if __name__ == "__main__":
    unittest.main()
