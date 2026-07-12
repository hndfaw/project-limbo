"""Observability: lifecycle events, run metrics, and redaction.

During a run the executor writes a JSONL **event log** (`events.jsonl`) capturing
every task lifecycle transition, and records **metrics** (counts and timings) in
the manifest. The CLI reads these back for `limbo inspect` (a manifest summary)
and `limbo timeline` (a readable execution timeline).

Environment metadata that reaches an event is passed through :func:`redact_env`
so secret-looking values (API keys, tokens, passwords) are never written to disk.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

REDACTED = "***redacted***"

# Env var names that almost always hold a secret.
_SECRET_NAME = re.compile(r"(SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY|_KEY$|^KEY$|CREDENTIAL|PRIVATE|AUTH|ACCESS[_-]?KEY)", re.IGNORECASE)
# Values that look like credentials regardless of their key.
_SECRET_VALUE = re.compile(r"^(sk-|ghp_|gho_|github_pat_|xox[baprs]-|AKIA|ASIA|-----BEGIN)")


def looks_secret(name: str, value: str) -> bool:
    """Whether an env entry looks like a secret by its name or value shape."""

    if _SECRET_NAME.search(name or ""):
        return True
    if isinstance(value, str) and _SECRET_VALUE.search(value):
        return True
    return False


def redact_env(env: Mapping[str, str]) -> Dict[str, str]:
    """Copy an env mapping, replacing secret-looking values with a placeholder."""

    return {name: (REDACTED if looks_secret(name, value) else value) for name, value in env.items()}


# Token shapes worth scrubbing out of free text (reasons, error messages).
_SECRET_TOKEN = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}"
    r"|ghp_[A-Za-z0-9]{8,}"
    r"|gho_[A-Za-z0-9]{8,}"
    r"|github_pat_[A-Za-z0-9_]{8,}"
    r"|xox[baprs]-[A-Za-z0-9-]{8,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|-----BEGIN [A-Z ]+PRIVATE KEY-----)"
)


def redact_text(text: Optional[str]) -> Optional[str]:
    """Replace secret-shaped tokens in a string Limbo generates (reasons, errors)."""

    if not isinstance(text, str):
        return text
    return _SECRET_TOKEN.sub(REDACTED, text)


class EventLog:
    """A thread-safe JSONL writer for task lifecycle events."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, **fields: Any) -> None:
        record = {"ts": time.time(), "type": event_type}
        for key, value in fields.items():
            if value is not None:
                record[key] = value
        line = json.dumps(record, sort_keys=True) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    @staticmethod
    def read(path: Path) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            return events
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events


@dataclass(frozen=True)
class RunMetrics:
    """Aggregate counts and timings for a run."""

    task_count: int
    succeeded: int
    failed: int
    skipped: int
    blocked: int
    cache_hits: int
    total_run_seconds: float
    total_queue_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_count": self.task_count,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "blocked": self.blocked,
            "cache_hits": self.cache_hits,
            "total_run_seconds": self.total_run_seconds,
            "total_queue_seconds": self.total_queue_seconds,
        }

    @classmethod
    def from_results(cls, results: Iterable[Any]) -> "RunMetrics":
        """Build metrics from TaskResult-like objects (duck-typed)."""

        results = list(results)
        by_status: Dict[str, int] = {}
        run_seconds = 0.0
        queue_seconds = 0.0
        for result in results:
            status = getattr(result, "status", None)
            by_status[status] = by_status.get(status, 0) + 1
            if status in ("succeeded", "failed", "skipped"):
                run_seconds += float(getattr(result, "duration_seconds", 0.0) or 0.0)
            queued_at = getattr(result, "queued_at", None)
            started_at = getattr(result, "started_at", None)
            if queued_at is not None and started_at is not None and started_at >= queued_at:
                queue_seconds += started_at - queued_at
        skipped = by_status.get("skipped", 0)
        return cls(
            task_count=len(results),
            succeeded=by_status.get("succeeded", 0),
            failed=by_status.get("failed", 0),
            skipped=skipped,
            blocked=by_status.get("blocked", 0),
            cache_hits=skipped,
            total_run_seconds=run_seconds,
            total_queue_seconds=queue_seconds,
        )

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any]) -> "RunMetrics":
        """Rebuild metrics from a persisted manifest's ``results`` list."""

        results = manifest.get("results", []) if isinstance(manifest, Mapping) else []
        objects = [_ResultView(item) for item in results if isinstance(item, Mapping)]
        return cls.from_results(objects)


class _ResultView:
    """Adapter exposing manifest result dicts as attribute access for from_results."""

    __slots__ = ("status", "duration_seconds", "queued_at", "started_at")

    def __init__(self, data: Mapping[str, Any]) -> None:
        self.status = data.get("status")
        self.duration_seconds = data.get("duration_seconds", 0.0)
        self.queued_at = data.get("queued_at")
        # Manifests don't persist started_at; queue time is derived live and
        # stored, so a manifest-only rebuild reports zero queue time.
        self.started_at = None


def build_timeline(events: Iterable[Mapping[str, Any]]) -> List[str]:
    """Render lifecycle events as readable, relative-time timeline lines."""

    events = [event for event in events if isinstance(event, Mapping) and "ts" in event]
    if not events:
        return []
    events.sort(key=lambda event: event["ts"])
    origin = events[0]["ts"]
    lines: List[str] = []
    for event in events:
        offset = event["ts"] - origin
        label = _timeline_label(event)
        lines.append(f"+{offset:7.3f}s  {label}")
    return lines


def _timeline_label(event: Mapping[str, Any]) -> str:
    event_type = event.get("type", "?")
    task_id = event.get("task_id")
    if event_type == "run_started":
        return f"run started ({event.get('task_count', 0)} task(s))"
    if event_type == "run_finished":
        return f"run finished: {event.get('status', 'unknown')}"
    action = event_type.replace("task_", "")
    detail = ""
    if event.get("returncode") is not None:
        detail = f" (exit {event['returncode']})"
    elif event.get("reason"):
        detail = f" ({event['reason']})"
    return f"{task_id}: {action}{detail}"
