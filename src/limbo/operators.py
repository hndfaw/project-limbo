"""Built-in tabular data operators for JSONL and CSV files."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from limbo.errors import LimboError, SpecError


class OperatorError(LimboError):
    """Raised when a built-in operator cannot process its data."""


def validate_operator(value: Any, task_id: str) -> Dict[str, Any]:
    """Validate and normalize a declarative operator configuration."""

    if not isinstance(value, Mapping):
        raise SpecError(f"task {task_id!r}: operator must be an object")
    config = dict(value)
    kind = config.get("type")
    if kind not in {"filter", "project", "join", "aggregate"}:
        raise SpecError(f"task {task_id!r}: unsupported operator type {kind!r}")
    if config.get("format") not in {"jsonl", "csv"}:
        raise SpecError(f"task {task_id!r}: operator format must be 'jsonl' or 'csv'")

    required = {"join": ("left", "right", "output"), "filter": ("input", "output", "where"),
                "project": ("input", "output", "fields"), "aggregate": ("input", "output", "aggregations")}[kind]
    for field in required:
        if field not in config:
            raise SpecError(f"task {task_id!r}: {kind} operator requires {field!r}")
    for field in ("input", "left", "right", "output"):
        if field in config and (not isinstance(config[field], str) or not config[field]):
            raise SpecError(f"task {task_id!r}: operator {field} must be a non-empty string")

    if kind == "filter":
        where = config["where"]
        if not isinstance(where, Mapping) or not isinstance(where.get("field"), str) or "equals" not in where:
            raise SpecError(f"task {task_id!r}: where requires field and equals")
    elif kind == "project":
        _string_list(config["fields"], task_id, "fields", allow_empty=False)
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
            rows = [row for row in source if row.get(config["where"]["field"]) == config["where"]["equals"]]
            fields = _csv_fields(config["input"], base_dir) if config["format"] == "csv" else None
        elif kind == "project":
            rows = [{field: row.get(field) for field in config["fields"]} for row in source]
            fields = config["fields"]
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
    right_fields = set()
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
            value = {"sum": sum, "min": min, "max": max}[op](values) if op != "avg" else sum(values) / len(values)
            output[name] = int(value) if value.is_integer() else value
        result.append(output)
    return result


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
