"""Task fingerprinting for cache-aware execution."""

from __future__ import annotations

import glob
import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, List

from limbo.spec import TaskSpec


FINGERPRINT_VERSION = 1


def task_fingerprint(task: TaskSpec, pipeline_base: Path) -> str:
    """Return a stable fingerprint for task configuration and declared inputs."""

    pipeline_base = Path(pipeline_base).resolve()
    payload = {
        "version": FINGERPRINT_VERSION,
        "id": task.id,
        "command": task.command,
        "cwd": task.cwd or "",
        "env": dict(sorted(task.env.items())),
        "inputs": _input_digests(task.inputs, pipeline_base),
        "outputs": list(task.outputs),
        "timeout_seconds": task.timeout_seconds,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def outputs_exist(task: TaskSpec, pipeline_base: Path) -> bool:
    """Return True when all declared outputs exist."""

    if not task.outputs:
        return False
    return all(_resolve(pipeline_base, output).exists() for output in task.outputs)


def _input_digests(patterns: Iterable[str], pipeline_base: Path) -> List[Dict[str, str]]:
    digests: List[Dict[str, str]] = []
    for pattern in patterns:
        matches = _expand(pattern, pipeline_base)
        if not matches:
            digests.append({"path": pattern, "sha256": "<missing>"})
            continue
        for path in matches:
            digests.append({"path": str(path.relative_to(pipeline_base)), "sha256": _sha256(path)})
    return digests


def _expand(pattern: str, pipeline_base: Path) -> List[Path]:
    absolute_pattern = str(_resolve(pipeline_base, pattern))
    return sorted(Path(item).resolve() for item in glob.glob(absolute_pattern, recursive=True) if Path(item).is_file())


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
