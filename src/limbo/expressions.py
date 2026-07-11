"""A safe expression evaluator for operator predicates and derived fields.

Expressions are parsed with :mod:`ast` and evaluated over an explicit
whitelist of node types. Raw :func:`eval` is never used, no attribute access
or subscripting is permitted, and only a fixed table of pure helper functions
can be called. Bare names resolve to fields of the current record.

The grammar intentionally supports just enough to express filter predicates
and derived columns: literals, field references, arithmetic, comparisons,
boolean and conditional logic, and a small set of scalar helper functions.
"""

from __future__ import annotations

import ast
import operator as _operator
from typing import Any, Callable, Dict, Mapping

from limbo.errors import LimboError


class ExpressionError(LimboError):
    """Raised when an expression is invalid or cannot be evaluated."""


_BIN_OPS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: _operator.add,
    ast.Sub: _operator.sub,
    ast.Mult: _operator.mul,
    ast.Div: _operator.truediv,
    ast.FloorDiv: _operator.floordiv,
    ast.Mod: _operator.mod,
    ast.Pow: _operator.pow,
}

_UNARY_OPS: Dict[type, Callable[[Any], Any]] = {
    ast.UAdd: _operator.pos,
    ast.USub: _operator.neg,
    ast.Not: _operator.not_,
}

_COMPARE_OPS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: _operator.eq,
    ast.NotEq: _operator.ne,
    ast.Lt: _operator.lt,
    ast.LtE: _operator.le,
    ast.Gt: _operator.gt,
    ast.GtE: _operator.ge,
    ast.In: lambda left, right: left in right,
    ast.NotIn: lambda left, right: left not in right,
}


def _as_bool(value: Any) -> bool:
    return bool(value)


_FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "lower": lambda value: str(value).lower(),
    "upper": lambda value: str(value).upper(),
    "strip": lambda value: str(value).strip(),
    "len": len,
    "abs": abs,
    "round": round,
    "int": int,
    "float": float,
    "str": str,
    "bool": _as_bool,
    "min": min,
    "max": max,
    "startswith": lambda value, prefix: str(value).startswith(prefix),
    "endswith": lambda value, suffix: str(value).endswith(suffix),
    "contains": lambda value, item: item in value,
    "coalesce": lambda *values: next((v for v in values if v is not None), None),
}

# A missing-field sentinel so predicates can distinguish "absent" from a real
# ``None`` value stored in the record.
_MISSING = object()


class Expression:
    """A compiled, reusable expression bound to a source string."""

    __slots__ = ("source", "_tree")

    def __init__(self, source: str) -> None:
        if not isinstance(source, str) or not source.strip():
            raise ExpressionError("expression must be a non-empty string")
        self.source = source
        try:
            parsed = ast.parse(source, mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"invalid expression {source!r}: {exc.msg}") from exc
        _validate_node(parsed.body, source)
        self._tree = parsed.body

    def evaluate(self, row: Mapping[str, Any]) -> Any:
        """Evaluate the expression against a single record."""

        return _eval_node(self._tree, row, self.source)

    def matches(self, row: Mapping[str, Any]) -> bool:
        """Evaluate the expression and return its truthiness (for predicates)."""

        return bool(self.evaluate(row))


def compile_expression(source: str) -> Expression:
    """Parse and validate ``source``, returning a reusable :class:`Expression`."""

    return Expression(source)


_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.And,
    ast.Or,
)


def _validate_node(node: ast.AST, source: str) -> None:
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ExpressionError(f"invalid expression {source!r}: only named function calls are allowed")
        if node.func.id not in _FUNCTIONS:
            raise ExpressionError(f"invalid expression {source!r}: unknown function {node.func.id!r}")
        if node.keywords:
            raise ExpressionError(f"invalid expression {source!r}: keyword arguments are not supported")
        for argument in node.args:
            if isinstance(argument, ast.Starred):
                raise ExpressionError(f"invalid expression {source!r}: argument unpacking is not supported")
            _validate_node(argument, source)
        return

    if isinstance(node, ast.BinOp) and type(node.op) not in _BIN_OPS:
        raise ExpressionError(f"invalid expression {source!r}: operator {type(node.op).__name__} is not allowed")
    if isinstance(node, ast.UnaryOp) and type(node.op) not in _UNARY_OPS:
        raise ExpressionError(f"invalid expression {source!r}: operator {type(node.op).__name__} is not allowed")
    if isinstance(node, ast.Compare):
        for op in node.ops:
            if type(op) not in _COMPARE_OPS:
                raise ExpressionError(f"invalid expression {source!r}: comparison {type(op).__name__} is not allowed")

    if not isinstance(node, _ALLOWED_NODES):
        raise ExpressionError(
            f"invalid expression {source!r}: {type(node).__name__} is not allowed"
        )

    for child in ast.iter_child_nodes(node):
        # Operator marker nodes (ast.Add, ast.Eq, ...) are validated above via
        # their parent; they are not themselves in the allowlist.
        if isinstance(child, (ast.operator, ast.unaryop, ast.cmpop, ast.boolop)):
            continue
        _validate_node(child, source)


def _eval_node(node: ast.AST, row: Mapping[str, Any], source: str) -> Any:
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        value = row.get(node.id, _MISSING)
        if value is _MISSING:
            raise ExpressionError(f"expression {source!r}: field {node.id!r} is not present in the record")
        return value

    if isinstance(node, ast.BoolOp):
        values = node.values
        if isinstance(node.op, ast.And):
            result: Any = True
            for value_node in values:
                result = _eval_node(value_node, row, source)
                if not result:
                    return result
            return result
        result = False
        for value_node in values:
            result = _eval_node(value_node, row, source)
            if result:
                return result
        return result

    if isinstance(node, ast.UnaryOp):
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand, row, source))

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, row, source)
        right = _eval_node(node.right, row, source)
        try:
            return _BIN_OPS[type(node.op)](left, right)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise ExpressionError(f"expression {source!r}: {exc}") from exc

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, row, source)
        for op, comparator_node in zip(node.ops, node.comparators):
            right = _eval_node(comparator_node, row, source)
            try:
                outcome = _COMPARE_OPS[type(op)](left, right)
            except TypeError as exc:
                raise ExpressionError(f"expression {source!r}: {exc}") from exc
            if not outcome:
                return False
            left = right
        return True

    if isinstance(node, ast.IfExp):
        if _eval_node(node.test, row, source):
            return _eval_node(node.body, row, source)
        return _eval_node(node.orelse, row, source)

    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_node(element, row, source) for element in node.elts]

    if isinstance(node, ast.Call):
        function = _FUNCTIONS[node.func.id]  # validated earlier
        arguments = [_eval_node(argument, row, source) for argument in node.args]
        try:
            return function(*arguments)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise ExpressionError(f"expression {source!r}: {node.func.id}(): {exc}") from exc

    raise ExpressionError(f"invalid expression {source!r}: {type(node).__name__} is not allowed")
