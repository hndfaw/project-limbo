"""Persistent task cache metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class CacheEntry:
    task_id: str
    fingerprint: str
    status: str
    run_id: str


class TaskCache:
    """Small JSON-backed cache keyed by task ID."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "cache.json"
        self._entries: Dict[str, CacheEntry] = {}
        self._loaded = False

    def get(self, task_id: str) -> Optional[CacheEntry]:
        self._load()
        return self._entries.get(task_id)

    def put(self, entry: CacheEntry) -> None:
        self._load()
        self._entries[entry.task_id] = entry
        self._flush()

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._entries = {}
            return
        if not isinstance(raw, dict):
            return
        for task_id, item in raw.items():
            if not isinstance(item, dict):
                continue
            fingerprint = item.get("fingerprint")
            status = item.get("status")
            run_id = item.get("run_id")
            if isinstance(task_id, str) and isinstance(fingerprint, str) and isinstance(status, str) and isinstance(run_id, str):
                self._entries[task_id] = CacheEntry(task_id, fingerprint, status, run_id)

    def _flush(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            task_id: {
                "fingerprint": entry.fingerprint,
                "status": entry.status,
                "run_id": entry.run_id,
            }
            for task_id, entry in sorted(self._entries.items())
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
