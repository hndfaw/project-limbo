"""Built-in tabular data operators for JSONL and CSV files."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from limbo.errors import LimboError, SpecError
from limbo.expressions import ExpressionError, compile_expression


class OperatorError(LimboError):
    """Raised when a built-in operator cannot process its data."""


def validate_operator(value: Any, task_id: str) -> Dict[str, Any]:
    """Validate and normalize a declarative operator configuration."""

    if not isinstance(value, Mapping):
        raise SpecError(f"task {task_id!r}: operator must be an object")
    config = dict(value)
    kind = config.get("type")
    if kind not in {"filter", "project", "rename", "derive", "join", "aggregate"}:
        raise SpecError(f"task {task_id!r}: unsupported operator type {kind!r}")
    if config.get("format") not in {"jsonl", "csv"}:
        raise SpecError(f"task {task_id!r}: operator format must be 'jsonl' or 'csv'")

    required = {"join": ("left", "right", "output"), "filter": ("input", "output"),
                "project": ("input", "output", "fields"), "aggregate": ("input", "output", "aggregations"),
                "rename": ("input", "output", "rename"), "derive": ("input", "output", "derived")}[kind]
    for field in required:
        if field not in config:
            raise SpecError(f"task {task_id!r}: {kind} operator requires {field!r}")
    for field in ("input", "left", "right", "output"):
        if field in config and (not isinstance(config[field], str) or not config[field]):
            raise SpecError(f"task {task_id!r}: operator {field} must be a non-empty string")

    if kind == "filter":
        has_where = "where" in config
        has_expr = "expr" in config
        if has_where == has_expr:
            raise SpecError(f"task {task_id!r}: filter requires exactly one of 'where' or 'expr'")
        if has_where:
            where = config["where"]
            if not isinstance(where, Mapping) or not isinstance(where.get("field"), str) or "equals" not in where:
                raise SpecError(f"task {task_id!r}: where requires field and equals")
        else:
            _validate_expr(config["expr"], task_id, "expr")
    elif kind == "project":
        _string_list(config["fields"], task_id, "fields", allow_empty=False)
    elif kind == "rename":
        mapping = config["rename"]
        if not isinstance(mapping, Mapping) or not mapping:
            raise SpecError(f"task {task_id!r}: rename must be a non-empty object")
        for old, new in mapping.items():
            if not isinstance(old, str) or not old or not isinstance(new, str) or not new:
                raise SpecError(f"task {task_id!r}: rename keys and values must be non-empty strings")
        targets = list(mapping.values())
        if len(set(targets)) != len(targets):
            raise SpecError(f"task {task_id!r}: rename maps multiple fields to the same name")
    elif kind == "derive":
        derived = config["derived"]
        if not isinstance(derived, Mapping) or not derived:
            raise SpecError(f"task {task_id!r}: derived must be a non-empty object")
        for name, expression in derived.items():
            if not isinstance(name, str) or not name:
                raise SpecError(f"task {task_id!r}: derived field names must be non-empty strings")
            _validate_expr(expression, task_id, f"derived[{name}]")
    elif kind == "join":
        if not isinstance(config.get("on"), str) or not config["on"]:
            raise SpecError(f"task {task_id!r}: join operator requires non-empty 'on'")
        if config.get("how", "inner") not in {"inner", "left"}:
            raise SpecError(f"task {task_id!r}: join how must be 'inner' or 'left'")
    else:
        _string_list(config.get("group_by", []), task_id, "group_by")
        aggregations = config["aggregations"]
        if not isinstance(aggregations, Mapping) or not aggregations:
            raise SpecError(f"task {task_id!r}: aggregations must be a non-empty object")
        for name, aggregation in aggregations.items():
            if not isinstance(name, str) or not name or not isinstance(aggregation, Mapping):
                raise SpecError(f"task {task_id!r}: invalid aggregation")
            if aggregation.get("op") not in {"count", "sum", "min", "max", "avg"}:
                raise SpecError(f"task {task_id!r}: unsupported aggregation {aggregation.get('op')!r}")
            if aggregation.get("op") != "count" and not isinstance(aggregation.get("field"), str):
                raise SpecError(f"task {task_id!r}: {name!r} aggregation requires field")
    return config


def operator_paths(config: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    inputs = [config[key] for key in ("input", "left", "right") if key in config]
    return inputs, [config["output"]]


def run_operator(config: Mapping[str, Any], base_dir: Path) -> int:
    """Execute an operator and return its output row count."""

    kind = config["type"]
    fields = None
    if kind == "join":
        rows = _join(_read(config["left"], config["format"], base_dir),
                     _read(config["right"], config["format"], base_dir), config)
        if config["format"] == "csv":
            left_fields = _csv_fields(config["left"], base_dir)
            right_fields = _csv_fields(config["right"], base_dir)
            fields = left_fields + [field if field not in left_fields else f"{field}_right"
                                    for field in right_fields if field != config["on"]]
    else:
        source = _read(config["input"], config["format"], base_dir)
        if kind == "filter":
            if "expr" in config:
                predicate = compile_expression(config["expr"])
                rows = _guard_expression(lambda: [row for row in source if predicate.matches(row)])
            else:
                rows = [row for row in source if row.get(config["where"]["field"]) == config["where"]["equals"]]
            fields = _csv_fields(config["input"], base_dir) if config["format"] == "csv" else None
        elif kind == "project":
            rows = [{field: row.get(field) for field in config["fields"]} for row in source]
            fields = config["fields"]
        elif kind == "rename":
            mapping = config["rename"]
            rows = [_apply_rename(row, mapping) for row in source]
            if config["format"] == "csv":
                input_fields = _csv_fields(config["input"], base_dir)
                fields = [mapping.get(field, field) for field in input_fields]
                if len(set(fields)) != len(fields):
                    raise OperatorError(f"rename produces duplicate column(s) in {config['output']!r}")
        elif kind == "derive":
            compiled = {name: compile_expression(expression) for name, expression in config["derived"].items()}
            rows = _guard_expression(lambda: _derive(source, compiled))
            if config["format"] == "csv":
                input_fields = _csv_fields(config["input"], base_dir)
                fields = input_fields + [name for name in config["derived"] if name not in input_fields]
        else:
            rows = _aggregate(source, config)
            fields = list(config.get("group_by", [])) + list(config["aggregations"])
    _write(rows, config["output"], config["format"], base_dir, fields)
    return len(rows)


def _read(name: str, data_format: str, base_dir: Path) -> List[Dict[str, Any]]:
    path = _resolve(name, base_dir)
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            if data_format == "csv":
                return [dict(row) for row in csv.DictReader(handle)]
            rows = []
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise OperatorError(f"{path}:{number}: JSONL row must be an object")
                rows.append(value)
            return rows
    except (OSError, csv.Error, json.JSONDecodeError) as exc:
        raise OperatorError(f"could not read {path}: {exc}") from exc


def _write(rows: Sequence[Mapping[str, Any]], name: str, data_format: str, base_dir: Path,
           fields: Optional[Sequence[str]] = None) -> None:
    path = _resolve(name, base_dir)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            if data_format == "jsonl":
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
            else:
                output_fields = list(fields) if fields is not None else (list(rows[0]) if rows else [])
                writer = csv.DictWriter(handle, fieldnames=output_fields)
                if output_fields:
                    writer.writeheader()
                    writer.writerows(rows)
        os.replace(temporary, path)
    except (OSError, csv.Error, TypeError, ValueError) as exc:
        try:
            if temporary is not None:
                os.unlink(temporary)
        except OSError:
            pass
        raise OperatorError(f"could not write {path}: {exc}") from exc


def _join(left: Iterable[Dict[str, Any]], right: Iterable[Dict[str, Any]], config: Mapping[str, Any]) -> List[Dict[str, Any]]:
    key = config["on"]
    index: Dict[Any, List[Dict[str, Any]]] = {}
    right_fields: Set[str] = set()
    for row in right:
        try:
            index.setdefault(row.get(key), []).append(row)
        except TypeError as exc:
            raise OperatorError(f"join field {key!r} must contain scalar values") from exc
        right_fields.update(row)
    result = []
    for left_row in left:
        try:
            matches = index.get(left_row.get(key), [])
        except TypeError as exc:
            raise OperatorError(f"join field {key!r} must contain scalar values") from exc
        if not matches and config.get("how", "inner") == "left":
            matches = [{field: None for field in right_fields}]
        for right_row in matches:
            merged = dict(left_row)
            for field, value in right_row.items():
                if field != key:
                    merged[field if field not in merged else f"{field}_right"] = value
            result.append(merged)
    return result


def _aggregate(rows: Iterable[Dict[str, Any]], config: Mapping[str, Any]) -> List[Dict[str, Any]]:
    group_fields = config.get("group_by", [])
    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for row in rows:
        try:
            groups.setdefault(tuple(row.get(field) for field in group_fields), []).append(row)
        except TypeError as exc:
            raise OperatorError("group_by fields must contain scalar values") from exc
    result = []
    for key, members in groups.items():
        output = dict(zip(group_fields, key))
        for name, aggregation in config["aggregations"].items():
            op = aggregation["op"]
            if op == "count":
                output[name] = len(members)
                continue
            try:
                values = [float(row[aggregation["field"]]) for row in members]
            except (KeyError, TypeError, ValueError) as exc:
                raise OperatorError(f"aggregation {name!r} requires numeric field {aggregation['field']!r}") from exc
            if op == "avg":
                value = sum(values) / len(values)
            elif op == "sum":
                value = sum(values)
            elif op == "min":
                value = min(values)
            else:  # max
                value = max(values)
            output[name] = int(value) if value.is_integer() else value
        result.append(output)
    return result


def _apply_rename(row: Mapping[str, Any], mapping: Mapping[str, str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in row.items():
        new_key = mapping.get(key, key)
        if new_key in result:
            raise OperatorError(f"rename produced duplicate field {new_key!r}")
        result[new_key] = value
    return result


def _derive(rows: Iterable[Mapping[str, Any]], compiled: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result = []
    for row in rows:
        derived = dict(row)
        for name, expression in compiled.items():
            derived[name] = expression.evaluate(row)
        result.append(derived)
    return result


def _guard_expression(action):
    """Run an expression-driven callable, mapping evaluation errors to OperatorError."""

    try:
        return action()
    except ExpressionError as exc:
        raise OperatorError(str(exc)) from exc


def _validate_expr(value: Any, task_id: str, label: str) -> None:
    if not isinstance(value, str):
        raise SpecError(f"task {task_id!r}: {label} must be a string expression")
    try:
        compile_expression(value)
    except ExpressionError as exc:
        raise SpecError(f"task {task_id!r}: {label}: {exc}") from exc


def _string_list(value: Any, task_id: str, name: str, allow_empty: bool = True) -> None:
    if not isinstance(value, list) or (not allow_empty and not value) or any(not isinstance(item, str) or not item for item in value):
        raise SpecError(f"task {task_id!r}: operator {name} must be a list of non-empty strings")


def _resolve(name: str, base_dir: Path) -> Path:
    path = Path(name)
    return path if path.is_absolute() else base_dir / path


def _csv_fields(name: str, base_dir: Path) -> List[str]:
    path = _resolve(name, base_dir)
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle).fieldnames or [])
    except (OSError, csv.Error) as exc:
        raise OperatorError(f"could not read {path}: {exc}") from exc
